r"""
============
quantnn.mrnn
============

This module implements Mixed Regression Neural Networks (MRSS), which
allow mixing quantile, density and MSE regression within a single
model.
"""
from abc import ABC

import numpy as np
from quantnn import quantiles as qq
from quantnn import density as qd
from quantnn.neural_network_model import NeuralNetworkModel
from quantnn.common import QuantnnException, UnsupportedBackendException
from quantnn.generic import softmax, to_array, get_array_module
from quantnn.utils import apply


###############################################################################
# Target class
###############################################################################


class Quantiles():
    """
    Represents a regression target for which a given selection of quantiles
    should be predicted.
    """
    def __init__(self, quantiles):
        """
        Args:
            quantiles: Array containing the quantiles to predict.
        """
        self.quantile_axis = 1
        self.quantiles = quantiles

    def get_loss(self, backend, mask=None):
        """
        Return loss function for this target for a specific backend.

        Args:
            backend: The backend from which to retrieve the loss
                function.
            mask: Optional mask value to use during training.

        Return:
            The loss function to use to train this target.
        """
        return backend.QuantileLoss(self.quantiles, mask=mask)

    def predict(self, y_pred):
        """
        Apply post processing to model prediction. Does nothing
        for predicted quantiles.
        """
        return y_pred

    def cdf(self, y_pred):
        """
        Calculate CDF from predicted quantiles.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.cdf(y_pred,
                      quantiles,
                      quantile_axis=self.quantile_axis)

    def pdf(self, y_pred):
        """
        Calculate PDF from predicted quantiles.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.pdf(y_pred,
                      quantiles,
                      quantile_axis=self.quantile_axis)

    def sample_posterior(self, y_pred, n_samples=1):
        """
        Sample retrieval values from posterior distribution.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
            n_samples: The number of samples to produce.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.sample_posterior(
            y_pred, quantiles, n_samples=n_samples,
            quantile_axis=self.quantile_axis
        )

    def sample_posterior_gaussian_fit(self, y_pred, n_samples=1):
        """
        Sample retrieval values from posterior distribution using
        a Gaussian fit.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
            n_samples: The number of samples to produce.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.sample_posterior_gaussian_fit(
            y_pred, quantiles, n_samples=n_samples,
            quantile_axis=self.quantile_axis
        )

    def posterior_mean(self, y_pred):
        """
        Calculate the posterior mean from predicted quantiles.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.posterior_mean(
            y_pred, quantiles, quantile_axis=self.quantile_axis
        )

    def crps(self, y_pred, y_true):
        """
        Calculate the CRPS score from predicted quantiles.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
            y_true: Tensor containing the true values.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.crps(
            y_pred, y_true, quantiles, quantile_axis=self.quantile_axis
        )

    def probability_larger_than(self, y_pred, y):
        """
        Calculate the probability that the retrieval value is larger than
        a given threshold.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
            y: The scalar threshold value.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.probability_larger_than(
            y_pred, quantiles, y,
            quantile_axis=self.quantile_axis
        )

    def probability_less_than(self, y_pred, y):
        """
        Calculate the probability that the retrieval value is less than
        a given threshold.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
            y: The scalar threshold value.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.probability_less_than(
            y_pred, quantiles, y,
            quantile_axis=self.quantile_axis
        )

    def posterior_quantiles(self, y_pred, new_quantiles):
        """
        Calculate new quantiles.

        Args:
            y_pred: Tensor containing the quantiles predicted by the NN
                model.
            new_quantiles: Array containing the new quantiles to compute.
        """
        module = get_array_module(y_pred)
        quantiles = to_array(module, self.quantiles, like=y_pred)
        return qq.posterior_quantiles(
            y_pred,
            quantiles=quantiles,
            new_quantiles=new_quantiles,
            quantile_axis=self.quantile_axis
        )

    def __repr__(self):
        return f"Quantiles({self.quantiles})"

    def __str__(self):
        return f"Quantiles({self.quantiles})"


class Density():
    """
    Represents a regression target for which a binned approximation of the
    probability density function should be predicted.
    """
    def __init__(self, bins, bin_axis=None):
        """
        Args:
            bins: Array defining the bin boundaries for the PDF approximation.
        """
        if bin_axis is None:
            self.bin_axis = 1
        self.bins = bins

    def get_loss(self, backend, mask=None):
        """
        Return loss function for this target for a specific backend.

        Args:
            backend: The backend from which to retrieve the loss
                function.
            mask: Optional mask value to use during training.

        Return:
            The loss function to use to train this target.
        """
        return backend.CrossEntropyLoss(self.bins, mask=mask)

    def predict(self, y_pred):
        """
        Apply post processing to model prediction. Converts predicted
        logits to normalized probabilities.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        y_pred = softmax(module, y_pred, axis=1)
        y_pred = qd.normalize(y_pred, bins, bin_axis=self.bin_axis)
        return y_pred

    def cdf(self, y_pred):
        """
        Calculate CDF from predicted logits.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        return qd.cdf(y_pred,
                      bins,
                      bin_axis=self.bin_axis)

    def pdf(self, y_pred):
        """
        Calculate PDF from predicted logits.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        return qd.pdf(y_pred,
                      bins,
                      bin_axis=self.bin_axis)

    def sample_posterior(self, y_pred, n_samples=1):
        """
        Sample retrieval values from posterior distribution.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
            n_samples: The number of samples to produce.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        return qd.sample_posterior(
            y_pred, bins, n_samples=n_samples,
            bin_axis=self.bin_axis
        )

    def sample_posterior_gaussian_fit(self, y_pred, n_samples=1):
        """
        Sample retrieval values from posterior distribution using
        a Gaussian fit.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
            n_samples: The number of samples to produce.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        return qd.sample_posterior_gaussian_fit(
            y_pred, bins, n_samples=n_samples,
            bin_axis=self.bin_axis
        )

    def posterior_mean(self, y_pred):
        """
        Calculate the posterior mean from predicted quantiles.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        return qd.posterior_mean(
            y_pred, bins,
            bin_axis=self.bin_axis
        )

    def crps(self, y_pred, y_true):
        """
        Calculate the CRPS score from predicted quantiles.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
            y_true: Tensor containing the true values.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        return qd.crps(
            y_pred, y_true, bins, bin_axis=self.bin_axis
        )

    def probability_larger_than(self, y_pred, y):
        """
        Calculate the probability that the retrieval value is larger than
        a given threshold.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
            y: The scalar threshold value.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        return qd.probability_larger_than(
            y_pred=y_pred, bins=bins, y=y,
            bin_axis=self.bin_axis
        )

    def probability_less_than(self, y_pred, y):
        """
        Calculate the probability that the retrieval value is larger than
        a given threshold.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
            y: The scalar threshold value.
        """
        return qd.probability_less_than(
            y_pred=y_pred, y=y,
            bin_axis=self.bin_axis
        )

    def posterior_quantiles(self, y_pred, new_quantiles):
        """
        Calculate quantiles of the posterior distribution.

        Args:
            y_pred: Tensor containing the logit values predicted by
                the neural network model.
            y: The scalar threshold value.
        """
        module = get_array_module(y_pred)
        bins = to_array(module, self.bins, like=y_pred)
        return qd.posterior_quantiles(
            y_pred,
            bins=bins,
            quantiles=new_quantiles,
            bin_axis=self.bin_axis
        )

    def __repr__(self):
        return f"Density({self.bins})"

    def __str__(self):
        return f"Density({self.bins})"

    def _post_process_prediction(self, y_pred, bins=None, key=None):
        module = get_array_module(y_pred)
        if bins is not None:
            bins = to_array(module, bins, like=y_pred)
        else:
            if isinstance(self.bins, dict):
                bins = to_array(module, self.bins[key], like=y_pred)
            else:
                bins = to_array(module, self.bins, like=y_pred)

        module = get_array_module(y_pred)
        y_pred = softmax(module, y_pred, axis=1)
        bins = to_array(module, bins, like=y_pred)
        y_pred = qd.normalize(y_pred, bins, bin_axis=self.bin_axis)
        return y_pred



class Mean():
    """
    Quantiles to predict.
    """
    def __init__(self):
        pass

    def predict(self, y_pred):
        return y_pred

    def get_loss(self, backend, mask=None):
        return backend.MSELoss(mask=mask)

    def posterior_mean(self, y_pred):
        return y_pred

    def __repr__(self):
        return f"Mean()"

    def __str__(self):
        return f"Mean()"


class MixedLoss:
    def __init__(self,
                 backend,
                 losses):
        self.losses = {
            k: l.get_loss(backend) for k, l in losses.items()
        }

    def to(self, device):
        for loss in self.losses.values():
            loss.to(device)

    def __call__(self, y_pred, y_true, key):
        return self.losses[key](y_pred, y_true, key)


###############################################################################
# MRNN class
###############################################################################


class MRNN(NeuralNetworkModel):
    r"""
    Mixed regression neural network.

    """
    def __init__(
        self, losses, n_inputs=None, model=None, transformation=None
    ):
        """
        Create a QRNN model.

        Arguments:
            n_inputs(int):
                The dimension of the measurement space, i.e. the
                number of elements in a single measurement vector y
            quantiles(np.array):
                1D-array containing the quantiles  to estimate of
                the posterior distribution. Given as fractions within the range
                [0, 1].
            model:
                A (possibly trained) model instance or a tuple ``(d, w, act)``
                describing the architecture of a fully-connected neural network
                with :code:`d` hidden layers with :code:`w` neurons and
                :code:`act` activation functions.
        """
        self.n_inputs = n_inputs
        self.losses = losses
        super().__init__(self.n_inputs, 0, model)
        self.quantile_axis = self.model.channel_axis
        self.transformation = transformation

    def train(
        self,
        training_data,
        validation_data=None,
        batch_size=None,
        optimizer=None,
        scheduler=None,
        n_epochs=None,
        adversarial_training=None,
        device="cpu",
        mask=None,
        logger=None,
        metrics=None,
        keys=None,
    ):
        """
        Train the underlying neural network model on given training data.

        The training data can be provided as either as tuples ``(x, y)``
        containing the raw input data as numpy arrays or as backend-specific
        dataset objects.

        .. note::

           If the train method doesn't serve your needs, the QRNN class can
           also be used with a pre-trained neural network.

        Args:
            training_data: Tuple of numpy arrays or backend-specific dataset
                object to use to train the model.
            validation_data: Optional validation data in the same format as the
                training data.
            batch_size: If training data is provided as arrays, this batch size
                will be used to for the training.
            optimizer: A backend-specific optimizer object to use for training.
            scheduler: A backend-specific scheduler object to use for training.
            n_epochs: The maximum number of epochs for which to train  the model.
            device: A ``str`` or backend-specific device object identifying the
                device to use for training.
            mask: Optional numeric value to use to mask all values that are
                smaller than or equal to this value.
            logger: A custom logger object to use to log training process. If
                not provided the default ``quantnn.training.TrainingLogger``
                class will be used.
            keys: Keys to use to determine input (``x``) and expected output
                 (``y``) when dataset elements are given as dictionaries.
        """
        #loss = MixedLoss(self.backend, self.losses)
        loss = {
            k: l.get_loss(self.backend, mask=mask)
            for k, l in self.losses.items()
        }
        return super().train(
            training_data,
            loss,
            validation_data=validation_data,
            optimizer=optimizer,
            scheduler=scheduler,
            n_epochs=n_epochs,
            adversarial_training=adversarial_training,
            batch_size=batch_size,
            device=device,
            logger=logger,
            metrics=metrics,
            keys=keys,
            transformation=self.transformation,
        )

    def predict(self, x):
        r"""
        Predict quantiles of the conditional distribution :math:`p(y|x)``.

        Forward propagates the inputs in ``x`` through the network to
        obtain the predicted quantiles ``y_pred``.

        Arguments:

            x(np.array): Rank-k tensor containing the input data with
                the input channels (or features) for each sample located
                along its first dimension.

        Returns:

            Rank-k tensor ``y_pred`` containing the quantiles of each input
            sample along its first dimension
        """

        def predict(x, loss, transformation):
            if transformation is not None:
                x = transformation.invert(x)
            return loss.predict(x)

        return apply(predict,
                     self.model.predict(x),
                     self.losses,
                     self.transformation)

    def cdf(self, x=None, y_pred=None, key=None):
        r"""
        Approximate the posterior CDF for given inputs ``x``.

        Propagates the inputs in ``x`` forward through the network and
        approximates the posterior CDF using a piecewise linear function.

        The piecewise linear function is given by its at quantiles
        :math:`y_{\tau_i}`` for :math:`\tau = \{0.0, \tau_1, \ldots,
        \tau_k, 1.0\}` where :math:`\tau_i` are the quantile fractions to be
        predicted by the network. The values for :math:`y_{\tau={0.0}}`
        and :math:`x_{\tau={1.0}}` are computed using

        .. math::

            y_{\tau=0.0} = 2.0 x_{\tau_1} - x_{\tau_2}

            y_{\tau=1.0} = 2.0 x_{\tau_k} - x_{\tau_{k-1}}

        Arguments:

            x: Rank-k tensor containing the input data with
                the input channels (or features) for each sample located
                along its first dimension.
            y_pred: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.

        Returns:

            Tuple ``(y_cdf, cdf)`` containing the abscissa-values ``y_cdf`` and
            the ordinates values ``cdf`` of the piece-wise linear approximation
            of the CDF :math:`F(y)`.

        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the input arguments x or y_pred must be " " provided."
                )
            y_pred = self.predict(x)

        if not isinstance(y_pred, dict):
            return self.losses[key].cdf(y_pred)

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "cdf"):
                results[k] = loss.cdf(y_pred[k])

        return results

    def pdf(self, x=None, y_pred=None, key=None):
        r"""
        Approximate the posterior probability density function (PDF) for given
        inputs ``x``.

        The PDF is approximated by computing the derivative of the piece-wise
        linear approximation of the CDF as computed by the
        :py:meth:`quantnn.QRNN.cdf` function.

        Arguments:

            x: Rank-k tensor containing the input data with
                the input channels (or features) for each sample located
                along its first dimension.
            y_pred: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.

        Returns:

            Tuple (x_pdf, y_pdf) containing the array with shape `(n, k)`  containing
            the x and y coordinates describing the PDF for the inputs in ``x``.

        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the input arguments x or y_pred must be " " provided."
                )
            y_pred = self.predict(x)

        if not isinstance(y_pred, dict):
            return self.losses[key].pdf(y_pred)

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "pdf"):
                results[k] = loss.pdf(y_pred[k])

        return results

    def sample_posterior(self, x=None, y_pred=None, n_samples=1, key=None):
        r"""
        Generates :code:`n` samples from the predicted posterior distribution
        for the input vector :code:`x`. The sampling is performed by the
        inverse CDF method using the predicted CDF obtained from the
        :code:`cdf` member function.

        Arguments:


            x: Rank-k tensor containing the input data with
                the input channels (or features) for each sample located
                along its first dimension.
            y_pred: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.
            n: The number of samples to generate.

        Returns:

            Rank-k tensor containing the random samples for each input sample
            along the first dimension.
        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the input arguments x or y_pred must be " " provided."
                )
            y_pred = self.predict(x)

        if not isinstance(y_pred, dict):
            return self.losses[key].sample_posterior(
                y_pred, n_samples=n_samples
            )

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "sample_posterior"):
                results[k] = loss.sample_posterior(y_pred[k], n_samples=n_samples)

        return results

    def sample_posterior_gaussian_fit(self, x=None, y_pred=None, n_samples=1, key=None):
        r"""
        Generates :code:`n` samples from the predicted posterior
        distribution for the input vector :code:`x`. The sampling
        is performed using a Gaussian fit to the predicted quantiles.

        Arguments:

            x: Rank-k tensor containing the input data with the input channels
                (or features) for each sample located along its first dimension.
            y_pred: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.
            n(int): The number of samples to generate.

        Returns:

            Tuple (xs, fs) containing the :math:`x`-values in `xs` and corresponding
            values of the posterior CDF :math:`F(x)` in `fs`.
        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the input arguments x or y_pred must be " " provided."
                )
            y_pred = self.predict(x)

        if not isinstance(y_pred, dict):
            return self.losses[key].sample_posterior_gaussian_fit(
                y_pred, n_samples=n_samples
            )

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "sample_posterior_gaussian_fit"):
                results[k] = loss.sample_posterior_gaussian_fit(
                    y_pred[k], n_samples=n_samples
                )

        return results

    def posterior_mean(self, x=None, y_pred=None, key=None):
        r"""
        Computes the posterior mean by computing the first moment of the
        predicted posterior CDF.

        Arguments:

            x: Rank-k tensor containing the input data with the input channels
                (or features) for each sample located along its first dimension.
            y_pred: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.
        Returns:

            Tensor or rank k-1 the posterior means for all provided inputs.
        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the input arguments x or y_pred must be " " provided."
                )
            y_pred = self.predict(x)

        if not isinstance(y_pred, dict):
            return self.losses[key].posterior_mean(y_pred)

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "posterior_mean"):
                results[k] = loss.posterior_mean(
                    y_pred[k]
                )

        return results

    def crps(self, x=None, y_pred=None, y_true=None, key=None):
        r"""
        Compute the Continuous Ranked Probability Score (CRPS).

        This function uses a piece-wise linear fit to the approximate posterior
        CDF obtained from the predicted quantiles in :code:`y_pred` to
        approximate the continuous ranked probability score (CRPS):

        .. math::
            \text{CRPS}(\mathbf{y}, x) = \int_{-\infty}^\infty (F_{x | \mathbf{y}}(x')
            - \mathrm{1}_{x < x'})^2 \: dx'

        Arguments:

            x: Rank-k tensor containing the input data with the input channels
                (or features) for each sample located along its first dimension.
            y_pred: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.
            y_true: Array containing the `n` true values, i.e. samples of the
                 true conditional distribution predicted by the QRNN.

            quantiles: 1D array containing the `k` quantile fractions :math:`\tau`
                       that correspond to the columns in `y_pred`.

        Returns:

            Tensor of rank k-1 containing the CRPS values for each of the samples.
        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the input arguments x or y_pred must be " " provided."
                )
            y_pred = self.predict(x)
        if y_true is None:
            raise ValueError(
                "The y_true argument must be provided to calculate "
                "the CRPS provided."
            )

        if not isinstance(y_pred, dict):
            loss = self.losses[key]
            if hasattr(loss, "crps"):
                return loss.crps(y_pred, y_true)
            else:
                return None

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "crps"):
                results[k] = loss.crps(y_pred[k], y_true[k])

        return results

    def probability_larger_than(self, x=None, y=None, y_pred=None, key=None):
        """
        Calculate probability of the output value being larger than a
        given numeric threshold.

        Args:
            x: Rank-k tensor containing the input data with the input channels
                (or features) for each sample located along its first dimension.
            y: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.
            y: The threshold value.

        Returns:

            Tensor of rank k-1 containing the for each input sample the
            probability of the corresponding y-value to be larger than the
            given threshold.
        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the input arguments x or y_pred must be " " provided."
                )
            y_pred = self.predict(x)
        if y is None:
            raise ValueError(
                "The y argument must be provided to compute the " " probability."
            )

        if not isinstance(y_pred, dict):
            loss = self.losses[key]
            if hasattr(loss, "probability_larger_than"):
                return loss.probability_larger_than(y_pred, y)
            return None

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "probability_larger_than"):
                results[k] = loss.probability_larger_than(
                    y_pred[k], y
                )

        return results

    def probability_less_than(self, x=None, y=None, y_pred=None, key=None):
        """
        Calculate probability of the output value being smaller than a
        given numeric threshold.

        Args:
            x: Rank-k tensor containing the input data with the input channels
                (or features) for each sample located along its first dimension.
            y_pred: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.
            y: The threshold value.

        Returns:

            Tensor of rank k-1 containing the for each input sample the
            probability of the corresponding y-value to be larger than the
            given threshold.
        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the input arguments x or y_pred must be " " provided."
                )
            y_pred = self.predict(x)

        if not isinstance(y_pred, dict):
            loss = self.losses[key]
            if hasattr(loss, "probability_less_than"):
                return loss.probability_less_than(y_pred, y)
            return None

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "probability_less_than"):
                results[k] = loss.probability_less_than(
                    y_pred[k], y
                )

        return results

    def posterior_quantiles(self, x=None, y_pred=None, quantiles=None, key=None):
        r"""
        Compute the posterior quantiles.

        Arguments:

            x: Rank-k tensor containing the input data with the input channels
                (or features) for each sample located along its first dimension.
            y_pred: Optional pre-computed quantile predictions, which, when
                 provided, will be used to avoid repeated propagation of the
                 the inputs through the network.
            new_quantiles: List of quantile fraction values :math:`\tau_i \in [0, 1]`.
        Returns:

            Rank-k tensor containing the desired predicted quantiles along its
            first dimension.
        """
        if y_pred is None:
            if x is None:
                raise ValueError(
                    "One of the keyword arguments 'x' or 'y_pred'" " must be provided."
                )
            y_pred = self.predict(x)

        if quantiles is None:
            raise ValueError(
                "The 'quantiles' keyword argument must be provided to"
                "calculate the posterior quantiles."
            )

        if not isinstance(y_pred, dict):
            loss = self.losses[key]
            if hasattr(loss, "posterior_quantiles"):
                return loss.posterior_quantiles(y_pred, quantiles)
            return None

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "posterior_quantiles"):
                results[k] = loss.posterior_quantiles(
                    y_pred[k], quantiles
                )

        return results

    def __setstate__(self, state):
        super().__setstate__(state)
        if not hasattr(self, "transformation"):
            self.transformation = None

    def _post_process_prediction(self, y_pred, bins=None, key=None):


        if not isinstance(y_pred, dict):
            loss = self.losses[key]
            if hasattr(loss, "_post_process_prediction"):
                return loss._post_process_prediction(y_pred, bins=bins, key=key)
            return y_pred

        results = {}
        for k in y_pred:
            loss = self.losses[k]
            if hasattr(loss, "_post_process_prediction"):
                results[k] = loss._post_process_prediction(y_pred, bins=bins, key=key)
        return results
