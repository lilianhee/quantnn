"""
quantnn.models.pytorch.common
=============================

This module provides common functionality required to realize QRNNs in pytorch.
"""
from inspect import signature
import os
import shutil
import tarfile
import tempfile

import torch
import numpy as np
from torch import nn
from torch import optim
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.utils.data import Dataset

from quantnn.common import ModelNotSupported
from quantnn.logging import TrainingLogger
from quantnn.data import BatchedDataset
from quantnn.backends.pytorch import PyTorch
from quantnn.generic import to_array

activations = {
    "elu": nn.ELU,
    "hardshrink": nn.Hardshrink,
    "hardtanh": nn.Hardtanh,
    "prelu": nn.PReLU,
    "relu": nn.ReLU,
    "selu": nn.SELU,
    "celu": nn.CELU,
    "sigmoid": nn.Sigmoid,
    "softplus": nn.Softplus,
    "softmin": nn.Softmin,
}


_ZERO_GRAD_ARGS = {}
major, minor, *_ = torch.__version__.split(".")
if int(major) >= 1 and int(minor) > 7:
    _ZERO_GRAD_ARGS = {"set_to_none": True}

def save_model(f, model):
    """
    Save pytorch model.

    Args:
        f(:code:`str` or binary stream): Either a path or a binary stream
            to store the data to.
        model(:code:`pytorch.nn.Moduel`): The pytorch model to save
    """
    path = tempfile.mkdtemp()
    filename = os.path.join(path, "keras_model.h5")
    torch.save(model, filename)
    archive = tarfile.TarFile(fileobj=f, mode="w")
    archive.add(filename, arcname="keras_model.h5")
    archive.close()
    shutil.rmtree(path)


def load_model(file):
    """
    Load pytorch model.

    Args:
        file(:code:`str` or binary stream): Either a path or a binary stream
            to read the model from
        quantiles(:code:`np.ndarray`): Array containing the quantiles
            that the model predicts.

    Returns:
        The loaded pytorch model.
    """
    path = tempfile.mkdtemp()
    tar_file = tarfile.TarFile(fileobj=file, mode="r")
    tar_file.extract("keras_model.h5", path=path)
    filename = os.path.join(path, "keras_model.h5")
    model = torch.load(filename, map_location=torch.device("cpu"))
    shutil.rmtree(path)
    return model


def handle_input(data, device=None):
    """
    Handle input data.

    This function handles data supplied

      - as tuple of :code:`np.ndarray`
      - a single :code:`np.ndarray`
      - torch :code:`dataloader`

    If a numpy array is provided it is converted to a torch tensor
    so that it can be fed into a pytorch model.
    """
    if type(data) == tuple:
        x, y = data

        dtype_y = torch.float
        if "int" in str(y.dtype):
            dtype_y = torch.long

        x = torch.tensor(x, dtype=torch.float)
        y = torch.tensor(y, dtype=dtype_y)
        if device is not None:
            x = x.to(device)
            y = y.to(device)
        return x, y

    if type(data) == np.ndarray:
        x = torch.tensor(data, dtype=torch.float)
        if device is not None:
            x = x.to(device)
        return x

    return data


class BatchedDataset(BatchedDataset):
    """
    Batches an un-batched dataset.
    """
    def __init__(self, training_data, batch_size=64):
        x, y = training_data
        super().__init__(x, y, batch_size, False, PyTorch)
#        self.n_samples = x.shape[0]
#
#        # x
#        if isinstance(x, torch.Tensor):
#            self.x = x.clone().detach().float()
#        else:
#            self.x = torch.tensor(x, dtype=torch.float)
#
#        # y
#        dtype_y = torch.float
#        if "int" in str(y.dtype):
#            dtype_y = torch.long
#        if isinstance(y, torch.Tensor):
#            self.y = y.clone().detach().to(dtype=dtype_y)
#        else:
#            self.y = torch.tensor(y, dtype=dtype_y)
#
#        if batch_size:
#            self.batch_size = batch_size
#        else:
#            self.batch_size = 256
#
#        self.indices = np.random.permutation(self.n_samples)
#
#    def __len__(self):
#        # This is required because x and y are tensors and don't throw these
#        # errors themselves.
#        return self.n_samples // self.batch_size
#
#    def __getitem__(self, i):
#        if (i == 0):
#            self.indices = np.random.permutation(self.n_samples)
#
#        if i >= len(self):
#            raise IndexError()
#        i_start = i * self.batch_size
#        i_end = (i + 1) * self.batch_size
#        indices = self.indices[i_start:i_end]
#        x = self.x[indices]
#        y = self.y[indices]
#        return (x, y)


################################################################################
# Quantile loss
################################################################################

class CrossEntropyLoss(nn.CrossEntropyLoss):
    """
    Cross entropy loss with optional masking.

    This loss function class calculates the mean cross entropy loss
    over the given inputs but applies an optional masking to the
    inputs, in order to allow the handling of missing values.
    """
    def __init__(self, mask=None):
        """
        Args:
            mask: All values that are smaller than or equal to this value will
                 be excluded from the calculation of the loss.
        """
        self.mask = mask
        if mask is None:
            reduction = "mean"
        else:
            reduction = "none"
        super().__init__(reduction=reduction)

    def __call__(self, y_pred, y_true):
        """Evaluate the loss."""
        y_true = y_true.long()
        if len(y_true.shape) == len(y_pred.shape):
            y_true = y_true.squeeze(1)

        if self.mask is None:
            return nn.CrossEntropyLoss.__call__(
                self,
                y_pred,
                y_true
            )
        else:
            loss = nn.CrossEntropyLoss.__call__(
                self,
                y_pred,
                torch.maximum(y_true, torch.zeros_like(y_true))
            )
            mask = y_true > self.mask
            return (loss * mask).sum() / mask.sum()


class QuantileLoss:
    r"""
    The quantile loss function

    This function object implements the quantile loss defined as


    .. math::

        \mathcal{L}(y_\text{pred}, y_\text{true}) =
        \begin{cases}
        \tau \cdot |y_\text{pred} - y_\text{true}| & , y_\text{pred} < y_\text{true} \\
        (1 - \tau) \cdot |y_\text{pred} - y_\text{true}| & , \text{otherwise}
        \end{cases}


    as a training criterion for the training of neural networks. The loss criterion
    expects a vector :math:`\mathbf{y}_\tau` of predicted quantiles and the observed
    value :math:`y`. The loss for a single training sample is computed by summing the losses
    corresponding to each quantiles. The loss for a batch of training samples is
    computed by taking the mean over all samples in the batch.
    """

    def __init__(self,
                 quantiles,
                 mask=None,
                 quantile_axis=1):
        """
        Create an instance of the quantile loss function with the given quantiles.

        Arguments:
            quantiles: Array or iterable containing the quantiles to be estimated.
        """
        self.quantiles = torch.tensor(quantiles).float()
        self.n_quantiles = len(quantiles)
        self.mask = mask
        if self.mask:
            self.mask = np.float32(mask)
        self.quantile_axis = quantile_axis

    def to(self, device):
        self.quantiles = self.quantiles.to(device)

    def __call__(self, y_pred, y_true):
        """
        Compute the mean quantile loss for given inputs.

        Arguments:
            y_pred: N-tensor containing the predicted quantiles along the last
                dimension
            y_true: (N-1)-tensor containing the true y values corresponding to
                the predictions in y_pred

        Returns:
            The mean quantile loss.
        """
        dy = y_pred - y_true
        n = self.quantiles.size()[0]

        shape = [1,] * len(dy.size())
        shape[self.quantile_axis] = self.n_quantiles
        qs = self.quantiles.reshape(shape)
        l = torch.where(dy >= 0.0, (1.0 - qs) * dy, (-qs) * dy)
        if self.mask:
            mask = y_true > self.mask
            return (l * mask).sum() / (mask.sum() * self.n_quantiles)
        return l.mean()

################################################################################
# Default scheduler and optimizer
################################################################################

def _get_default_optimizer(model):
    """
    The default optimizer. Currently set to Adam optimizer.
    """
    optimizer = optim.Adam(model.parameters())
    return optimizer

def _get_default_scheduler(optimizer):
    """
    The default scheduler which reduces lr when training loss reaches a
    plateau.
    """
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer,
                                                     factor=0.1,
                                                     patience=5)
    return scheduler

def _has_channels_last_tensor(parameters):
    """
    Determine whether any of the tensors in the models parameters is
    in channels last format.
    """
    for p in parameters:
        if isinstance(p.data, torch.Tensor):
            t = p.data
            if t.is_contiguous(memory_format=torch.channels_last) and not t.is_contiguous():
                return True
            elif isinstance(t, list) or isinstance(t, tuple):
                if _has_channels_last_tensor(list(t)):
                    return True
    return False

################################################################################
# QRNN
################################################################################


class PytorchModel:
    """
    Quantile regression neural network (QRNN)

    This class implements QRNNs as a fully-connected network with
    a given number of layers.
    """
    @staticmethod
    def create(model):
        if not isinstance(model, torch.nn.Module):
            raise ModelNotSupported(
                f"The provided model ({model}) is not supported by the PyTorch"
                "backend")
        if isinstance(model, PytorchModel):
            return model
        model.__class__ = type("__QuantnnMixin__", (PytorchModel, type(model)), {})
        PytorchModel.__init__(model)
        return model

    @property
    def channel_axis(self):
        """
        The index of the axis that contains the channel information in a batch
        of input data.
        """
        if _has_channels_last_tensor(self.parameters()):
            return -1
        return 1

    def __init__(self):
        """
        Arguments:
            input_dimension(int): The number of input features.
            quantiles(array): Array of the quantiles to predict.
        """
        self.training_errors = []
        self.validation_errors = []

    def _make_adversarial_samples(self, x, y, eps):
        self.zero_grad(**_ZERO_GRAD_ARGS)
        x.requires_grad = True
        y_pred = self(x)
        c = self.criterion(y_pred, y)
        c.backward()
        x_adv = x.detach() + eps * torch.sign(x.grad.detach())
        return x_adv

    def reset(self):
        """
        Reinitializes the weights of a model.
        """

        def reset_function(module):
            if isinstance(m, nn.Conv2d) or isinstance(m, nn.Linear):
                m.reset_parameters()

        self.apply(reset_function)

    def train(self,
              training_data,
              validation_data=None,
              loss=None,
              optimizer=None,
              scheduler=None,
              n_epochs=None,
              adversarial_training=None,
              batch_size=None,
              device='cpu'):
        """
        Train the network.

        This trains the network for the given number of epochs using the
        provided training and validation data.

        If desired, the training can be augmented using adversarial training.
        In this case the network is additionally trained with an adversarial
        batch of examples in each step of the training.

        Arguments:
            training_data: pytorch dataloader providing the training data
            validation_data: pytorch dataloader providing the validation data
            n_epochs: the number of epochs to train the network for
            adversarial_training: whether or not to use adversarial training
            eps_adv: The scaling factor to use for adversarial training.
        """
        # Avoid nameclash with PyTorch train method.
        if type(training_data) == bool:
            return nn.Module.train(self, training_data)

        log = TrainingLogger(n_epochs)

        # Determine device to use
        if torch.cuda.is_available() and device in ["gpu", "cuda"]:
            device = torch.device("cuda")
        elif device == "cpu":
            device = torch.device("cpu")
        else:
            device = torch.device(device)

        # Handle input data
        try:
            x, y = handle_input(training_data, device)
            training_data = BatchedDataset((x, y), batch_size=batch_size)
        except:
            pass

        try:
            x, y = handle_input(validation_data, device)
            validation_data = BatchedDataset((x, y), batch_size=batch_size)
        except:
            pass

        # Optimizer
        if not optimizer:
            optimizer = _get_default_optimizer(self)
        self.optimizer = optimizer

        # Training scheduler
        if not scheduler:
            scheduler = _get_default_scheduler(optimizer)

        loss.to(device)
        self.to(device)
        scheduler_sig = signature(scheduler.step)
        training_errors = []
        validation_errors = []

        state = {}
        for m in self.modules():
            state[m] = m.training

        # Training loop
        for i in range(n_epochs):
            error = 0.0
            n = 0
            for j, (x, y) in enumerate(training_data):

                x = x.float().to(device)
                y = y.to(device)

                shape = x.size()
                shape = (shape[0], 1) + shape[2:]
                y = y.reshape(shape)

                self.optimizer.zero_grad(**_ZERO_GRAD_ARGS)
                y_pred = self(x)
                c = loss(y_pred, y)
                c.backward()
                self.optimizer.step()

                error += c.item() * x.size()[0]
                n += x.size()[0]

                if adversarial_training:
                    self.optimizer.zero_grad(set_to_none=True)
                    x_adv = self._make_adversarial_samples(x, y, adversarial_training)
                    y_pred = self(x_adv)
                    c = loss(y_pred, y)
                    c.backward()
                    self.optimizer.step()

                #
                # Log training step.
                #
                if hasattr(training_data, "__len__"):
                    of = len(training_data)
                else:
                    of = None
                n_samples = torch.numel(x) / x.size()[1]
                log.training_step(c.item(), n_samples, of=of)

            # Save training error
            training_errors.append(error / n)

            lr = [group["lr"] for group in self.optimizer.param_groups][0]

            validation_error = 0.0
            if validation_data is not None:
                n = 0
                self.eval()
                with torch.no_grad():
                    for j, (x, y) in enumerate(validation_data):
                        x = x.to(device).detach()
                        y = y.to(device).detach()

                        shape = x.size()
                        shape = (shape[0], 1) + shape[2:]
                        y = y.reshape(shape)

                        y_pred = self(x)
                        c = loss(y_pred, y)

                        validation_error += c.item() * x.size()[0]
                        n += x.size()[0]

                        #
                        # Log validation step.
                        #
                        if hasattr(validation_data, "__len__"):
                            of = len(validation_data)
                        else:
                            of = None
                        n_samples = torch.numel(x) / x.size()[1]
                        log.validation_step(c.item(), n_samples, of=of)

                    validation_errors.append(validation_error / n)
                    for m in self.modules():
                        m.training = state[m]

                    if scheduler:
                        if len(scheduler_sig.parameters) == 1:
                            scheduler.step()
                        else:
                            if validation_data:
                                scheduler.step(validation_errors[-1])

            else:
                if scheduler:
                    if len(scheduler_sig.parameters) == 1:
                        scheduler.step()
                    else:
                        if validation_data:
                            scheduler.step(training_errors[-1])

            log.epoch(learning_rate=lr)

        self.training_errors += training_errors
        self.validation_errors += validation_errors
        self.eval()
        return {
            "training_errors": self.training_errors,
            "validation_errors": self.validation_errors,
        }

    def predict(self, x, device="cpu"):
        """
        Evaluate the model.

        Args:
            x: The input data for which to evaluate the data.
            device: The device on which to evaluate the prediction.

        Returns:
            The model prediction converted to numpy array.
        """
        # Determine device to use
        w = next(iter(self.parameters())).data
        if isinstance(x, torch.Tensor):
            x_torch = x
        else:
            x_torch = to_array(torch, x, like=w)
        self.to(x_torch.device)

        if x_torch.requires_grad:
            y = self(x_torch)
        else:
            with torch.no_grad():
                y = self(x_torch)

        return y

    def calibration(self, data, gpu=False):
        """
        Computes the calibration of the predictions from the neural network.

        Arguments:
            data: torch dataloader object providing the data for which to compute
                the calibration.

        Returns:
            (intervals, frequencies): Tuple containing the confidence intervals and
                corresponding observed frequencies.
        """

        if gpu and torch.cuda.is_available():
            dev = torch.device("cuda")
        else:
            dev = torch.device("cpu")
        self.to(dev)

        n_intervals = self.quantiles.size // 2
        qs = self.quantiles
        intervals = np.array([q_r - q_l for (q_l, q_r) in zip(qs, reversed(qs))])[
            :n_intervals
        ]
        counts = np.zeros(n_intervals)

        total = 0.0

        for x, y in iterator:
            x = x.to(dev).detach()
            y = y.to(dev).detach()
            shape = x.size()
            shape = (shape[0], 1) + shape[2:]
            y = y.reshape(shape)

            y_pred = self(x)
            y_pred = y_pred.cpu()
            y = y.cpu()

            for i in range(n_intervals):
                l = y_pred[:, [i]]
                r = y_pred[:, [-(i + 1)]]
                counts[i] += np.logical_and(y >= l, y < r).sum()

            total += np.prod(y.size())
        return intervals[::-1], (counts / total)[::-1]

    def save(self, path):
        """
        Save QRNN to file.

        Arguments:
            The path in which to store the QRNN.
        """
        torch.save(
            {
                "width": self.width,
                "depth": self.depth,
                "activation": self.activation,
                "network_state": self.state_dict(),
                "optimizer_state": self.optimizer.state_dict(),
            },
            path,
        )

    @staticmethod
    def load(self, path):
        """
        Load QRNN from file.

        Arguments:
            path: Path of the file where the QRNN was stored.
        """
        state = torch.load(path, map_location=torch.device("cpu"))
        keys = ["depth", "width", "activation"]
        qrnn = QRNN(*[state[k] for k in keys])
        qrnn.load_state_dict["network_state"]
        qrnn.optimizer.load_state_dict["optimizer_state"]
