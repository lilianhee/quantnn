"""
===============================
quantnn.models.pytorch.xception
===============================

PyTorch neural network models based on the Xception architecture.
"""
import torch
from torch import nn

######################################################
def _get_normalisation(normalisation, features):
        if normalisation == 'bn':
            return nn.BatchNorm2d(features)
        elif normalisation == 'ln':
            return nn.GroupNorm(1, features)
        elif normalisation == 'in':
            return nn.InstanceNorm2d(features)
        else:
            return nn.Identity()
#########################################################
class SymmetricPadding(nn.Module):
    """
    Network module implementing symmetric padding.

    This is just a wrapper around torch's ``nn.functional.pad`` with mode
    set to 'replicate'.
    """

    def __init__(self, amount):
        super().__init__()
        if isinstance(amount, int):
            self.amount = [amount] * 4
        else:
            self.amount = amount

    def forward(self, x):
        return nn.functional.pad(x, self.amount, "replicate")


class SeparableConv3x3(nn.Sequential):
    """
    Depth-wise separable convolution using with kernel size 3x3.
    """

    def __init__(self, channels_in, channels_out):
        super().__init__(
            nn.Conv2d(
                channels_in,
                channels_in,
                kernel_size=3,
                groups=channels_in,
                padding=1,
                padding_mode="replicate",
            ),
            nn.Conv2d(channels_in, channels_out, kernel_size=1),
        )


class XceptionBlock(nn.Module):
    """
    Xception block consisting of two depth-wise separable convolutions
    each folowed by batch-norm and GELU activations.
    """

    def __init__(self, channels_in, channels_out, downsample=False, normalisation = 'ln'):
        """
        Args:
            channels_in: The number of incoming channels.
            channels_out: The number of outgoing channels.
            downsample: Whether or not to insert 3x3 max pooling block
                after the first convolution.
        """
        super().__init__()
        if downsample:
            self.block_1 = nn.Sequential(
                SeparableConv3x3(channels_in, channels_out),
                #nn.GroupNorm(1, channels_out),
                _get_normalisation(normalisation,channels_out),
                SymmetricPadding(1),
                nn.MaxPool2d(kernel_size=3, stride=2),
                nn.GELU(),
            )
        else:
            self.block_1 = nn.Sequential(
                SeparableConv3x3(channels_in, channels_out),
                #nn.GroupNorm(1, channels_out),
                _get_normalisation(normalisation,channels_out),
                nn.GELU(),
            )

        self.block_2 = nn.Sequential(
            SeparableConv3x3(channels_out, channels_out),
            #nn.GroupNorm(1, channels_out),
            _get_normalisation(normalisation,channels_out),
            nn.GELU(),
        )

        if channels_in != channels_out or downsample:
            if downsample:
                self.projection = nn.Conv2d(channels_in, channels_out, 1, stride=2)
            else:
                self.projection = nn.Conv2d(channels_in, channels_out, 1)
        else:
            self.projection = None

    def forward(self, x):
        """
        Propagate input through block.
        """
        if self.projection is None:
            x_proj = x
        else:
            x_proj = self.projection(x)
        y = self.block_2(self.block_1(x))
        return x_proj + y


class DownsamplingBlock(nn.Sequential):
    """
    Xception downsampling block.
    """

    def __init__(self, n_channels, n_blocks,normalisation = 'ln'):
        blocks = [XceptionBlock(n_channels, n_channels, downsample=True,normalisation=normalisation)]
        for i in range(n_blocks):
            blocks.append(XceptionBlock(n_channels, n_channels,normalisation=normalisation))
        super().__init__(*blocks)


class UpsamplingBlock(nn.Module):
    """
    Xception upsampling block.
    """

    def __init__(self, n_channels, normalisation = 'ln'):
        """
        Args:
            n_channels: The number of incoming and outgoing channels.
        """
        super().__init__()
        self.upsample = nn.Upsample(mode="bilinear",
                                    scale_factor=2,
                                    align_corners=False)
        self.block = nn.Sequential(
            SeparableConv3x3(n_channels * 2, n_channels),
            #nn.GroupNorm(1, n_channels),
            _get_normalisation(normalisation,n_channels),
            nn.GELU(),
        )

    def forward(self, x, x_skip):
        """
        Propagate input through block.
        """
        x_up = self.upsample(x)
        x_merged = torch.cat([x_up, x_skip], 1)
        return self.block(x_merged)


class XceptionFpn(nn.Module):
    """
    Feature pyramid network (FPN) with 5 stages based on xception
    architecture.
    """

    def __init__(self, n_inputs, n_outputs, n_features=128, blocks=2, normalisation = 'ln'):
        """
        Args:
            n_inputs: Number of input channels.
            n_outputs: The number of output channels,
            n_features: The number of features in the xception blocks.
            blocks: The number of blocks per stage
        """
        super().__init__()

        if isinstance(blocks, int):
            blocks = [blocks] * 5

        self.in_block = nn.Conv2d(n_inputs, n_features, 1)

        self.down_block_2 = DownsamplingBlock(n_features, blocks[0],normalisation=normalisation)
        self.down_block_4 = DownsamplingBlock(n_features, blocks[1],normalisation=normalisation)
        self.down_block_8 = DownsamplingBlock(n_features, blocks[2],normalisation=normalisation)
        self.down_block_16 = DownsamplingBlock(n_features, blocks[3],normalisation=normalisation)
        self.down_block_32 = DownsamplingBlock(n_features, blocks[4],normalisation=normalisation)

        self.up_block_16 = UpsamplingBlock(n_features,normalisation=normalisation)
        self.up_block_8 = UpsamplingBlock(n_features,normalisation=normalisation)
        self.up_block_4 = UpsamplingBlock(n_features,normalisation=normalisation)
        self.up_block_2 = UpsamplingBlock(n_features,normalisation=normalisation)
        self.up_block = UpsamplingBlock(n_features,normalisation=normalisation)

        self.head = nn.Sequential(
            nn.Conv2d(2 * n_features, n_features, 1),
            #nn.GroupNorm(1, n_features),
            _get_normalisation(normalisation,n_features),
            nn.GELU(),
            nn.Conv2d(n_features, n_features, 1),
            #nn.GroupNorm(1, n_features),
            _get_normalisation(normalisation,n_features),
            nn.GELU(),
            nn.Conv2d(n_features, n_outputs, 1),
        )

    def forward(self, x):
        """
        Propagate input through block.
        """
        x_in = self.in_block(x)
        x_2 = self.down_block_2(x_in)
        x_4 = self.down_block_4(x_2)
        x_8 = self.down_block_8(x_4)
        x_16 = self.down_block_16(x_8)
        x_32 = self.down_block_32(x_16)

        x_16_u = self.up_block_16(x_32, x_16)
        x_8_u = self.up_block_8(x_16_u, x_8)
        x_4_u = self.up_block_4(x_8_u, x_4)
        x_2_u = self.up_block_2(x_4_u, x_2)
        x_u = self.up_block(x_2_u, x_in)

        return self.head(torch.cat([x_in, x_u], 1))