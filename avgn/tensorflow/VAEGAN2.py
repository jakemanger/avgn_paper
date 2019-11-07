import tensorflow as tf
import tensorflow_probability as tfp
import matplotlib.pyplot as plt
import numpy as np

PI = tf.Variable(np.pi)

ds = tfp.distributions


class VAEGAN(tf.keras.Model):
    """a VAEGAN class for tensorflow
    
    Extends:
        tf.keras.Model
    """

    def __init__(self, beta=1.0, **kwargs):
        super(VAEGAN, self).__init__()
        self.__dict__.update(kwargs)
        self.beta = beta

        self.enc = tf.keras.Sequential(self.enc)
        self.dec = tf.keras.Sequential(self.dec)
        inputs, disc_l, outputs = self.vae_disc_function()
        self.disc = tf.keras.Model(inputs=[inputs], outputs=[outputs, disc_l])

        if not hasattr(self, "enc_optimizer"):
            self.enc_optimizer = tf.keras.optimizers.Adam(self.lr_base_gen)
            self.dec_optimizer = tf.keras.optimizers.Adam(self.lr_base_gen)
            self.disc_optimizer = tf.keras.optimizers.Adam(self.get_lr_d)

    @tf.function
    def encode(self, x):
        mean, logvar = tf.split(self.enc(x), num_or_size_splits=2, axis=1)
        return mean, logvar

    @tf.function
    def dist_encode(self, x):
        mu, sigma = self.encode(x)
        return ds.MultivariateNormalDiag(loc=mu, scale_diag=sigma)

    @tf.function
    def get_lr_d(self):
        return self.lr_base_disc * self.D_prop

    @tf.function
    def decode(self, z, apply_sigmoid=False):
        logits = self.dec(z)
        if apply_sigmoid:
            probs = tf.sigmoid(logits)
            return probs
        return logits

    @tf.function
    def discriminate(self, x):
        return self.disc(x)

    def reconstruct(self, x):
        mu, _ = tf.split(self.enc(x), num_or_size_splits=2, axis=1)
        return self.decode(mu, apply_sigmoid=True)

    @tf.function
    def reparameterize(self, mean, logvar):
        eps = tf.random.normal(shape=mean.shape)
        return eps * tf.exp(logvar * 0.5) + mean

    # @tf.function
    def compute_loss(self, x):
        # pass through network
        mean, logvar = self.encode(x)
        z = self.reparameterize(mean, logvar)
        xg = self.decode(z, apply_sigmoid=True)

        z_samp = tf.random.normal([x.shape[0], 1, 1, z.shape[-1]])
        xg_samp = self.decode(z_samp, apply_sigmoid=True)
        _, ld_xg = self.discriminate(xg)
        d_x, ld_x = self.discriminate(x)
        d_xg_samp, _ = self.discriminate(xg_samp)

        # GAN losses
        disc_real_loss = gan_loss(logits=d_x, is_real=True)
        disc_fake_loss = gan_loss(logits=d_xg_samp, is_real=False)
        gen_fake_loss = gan_loss(logits=d_xg_samp, is_real=True)

        discrim_layer_recon_loss = (
            tf.reduce_mean(tf.reduce_mean(tf.math.square(ld_x - ld_xg), axis=0))
            / self.recon_loss_div
        )

        self.D_prop = sigmoid(
            disc_fake_loss - gen_fake_loss, shift=0.0, mult=self.sig_mult
        )

        # kl
        logpz = log_normal_pdf(z, 0.0, 0.0)
        logqz_x = log_normal_pdf(z, mean, logvar) * self.beta
        latent_loss = -tf.reduce_mean(logpz - logqz_x)

        enc_loss = latent_loss + discrim_layer_recon_loss
        dec_loss = gen_fake_loss + discrim_layer_recon_loss
        disc_loss = disc_fake_loss + self.D_prop * disc_real_loss

        return (
            self.D_prop,
            latent_loss,
            discrim_layer_recon_loss,
            gen_fake_loss,
            disc_fake_loss,
            disc_real_loss,
            enc_loss,
            dec_loss,
            disc_loss,
        )

    # @tf.function
    def compute_gradients(self, x):
        with tf.GradientTape() as enc_tape, tf.GradientTape() as dec_tape, tf.GradientTape() as disc_tape:
            (_, _, _, _, _, _, enc_loss, dec_loss, disc_loss) = self.compute_loss(x)

        enc_gradients = enc_tape.gradient(enc_loss, self.enc.trainable_variables)
        dec_gradients = dec_tape.gradient(dec_loss, self.dec.trainable_variables)
        disc_gradients = disc_tape.gradient(disc_loss, self.disc.trainable_variables)

        return enc_gradients, dec_gradients, disc_gradients

    @tf.function
    def apply_gradients(self, enc_gradients, dec_gradients, disc_gradients):
        self.enc_optimizer.apply_gradients(
            zip(enc_gradients, self.enc.trainable_variables)
        )
        self.dec_optimizer.apply_gradients(
            zip(dec_gradients, self.dec.trainable_variables)
        )
        self.disc_optimizer.apply_gradients(
            zip(disc_gradients, self.disc.trainable_variables)
        )

    @tf.function
    def train_net(self, x):
        enc_gradients, dec_gradients, disc_gradients = self.compute_gradients(x)
        self.apply_gradients(enc_gradients, dec_gradients, disc_gradients)


@tf.function
def log_normal_pdf(sample, mean, logvar, raxis=1):
    log2pi = tf.math.log(2.0 * PI)
    return tf.reduce_sum(
        -0.5 * ((sample - mean) ** 2.0 * tf.exp(-logvar) + logvar + log2pi), axis=raxis
    )


# @tf.function
def gan_loss(logits, is_real=True):
    """Computes standard gan loss between logits and labels
                
        Arguments:
            logits {[type]} -- output of discriminator
        
        Keyword Arguments:
            isreal {bool} -- whether labels should be 0 (fake) or 1 (real) (default: {True})
        """
    if is_real:
        labels = tf.ones_like(logits)
    else:
        labels = tf.zeros_like(logits)
    # tf.keras.backend.binary_crossentropy(labels, logits)
    # return tf.compat.v1.losses.sigmoid_cross_entropy(
    #    multi_class_labels=labels, logits=logits
    # )
    return tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(labels, logits))


def sigmoid(x, shift=0.0, mult=20):
    """ squashes a value with a sigmoid
    """
    return tf.constant(1.0) / (
        tf.constant(1.0) + tf.exp(-tf.constant(1.0) * (x * mult))
    )


def plot_reconstruction(model, example_data, BATCH_SIZE, N_Z, nex=8, zm=2):

    example_data_reconstructed = model.reconstruct(example_data)
    samples = model.decode(
        tf.random.normal(shape=(BATCH_SIZE, N_Z)), apply_sigmoid=True
    )
    fig, axs = plt.subplots(ncols=nex, nrows=3, figsize=(zm * nex, zm * 3))
    for axi, (dat, lab) in enumerate(
        zip(
            [example_data, example_data_reconstructed, samples],
            ["data", "data recon", "samples"],
        )
    ):
        for ex in range(nex):
            axs[axi, ex].matshow(
                dat.numpy()[ex].squeeze(), cmap=plt.cm.Greys, vmin=0, vmax=1
            )
            axs[axi, ex].axes.get_xaxis().set_ticks([])
            axs[axi, ex].axes.get_yaxis().set_ticks([])
        axs[axi, 0].set_ylabel(lab)

    plt.show()


def plot_losses(losses):
    fig, axs = plt.subplots(ncols=4, nrows=1, figsize=(16, 4))
    axs[0].plot(losses.latent_loss.values, label="latent_loss")
    axs[1].plot(
        losses.discrim_layer_recon_loss.values, label="discrim_layer_recon_loss"
    )
    axs[2].plot(losses.disc_real_loss.values, label="disc_real_loss")
    axs[2].plot(losses.disc_fake_loss.values, label="disc_fake_loss")
    axs[2].plot(losses.gen_fake_loss.values, label="gen_fake_loss")
    axs[3].plot(losses.d_prop.values, label="d_prop")

    for ax in axs.flatten():
        ax.legend()
    plt.show()