# from https://github.com/espnet/espnet/blob/master/espnet2/gan_tts/vits/duration_predictor.py

# Copyright 2021 Tomoki Hayashi
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

"""Stochastic duration predictor modules in VITS.

This code is based on https://github.com/jaywalnut310/vits.

"""

import math
from typing import Optional

import torch
import torch.nn.functional as F
from flow import (
    ConvFlow,
    DilatedDepthSeparableConv,
    ElementwiseAffineFlow,
    FlipFlow,
    LogFlow,
)


class StochasticDurationPredictor(torch.nn.Module):
    """Stochastic duration predictor module.

    This is a module of stochastic duration predictor described in `Conditional
    Variational Autoencoder with Adversarial Learning for End-to-End Text-to-Speech`_.

    .. _`Conditional Variational Autoencoder with Adversarial Learning for End-to-End
        Text-to-Speech`: https://arxiv.org/abs/2006.04558

    """

    def __init__(
        self,
        channels: int = 192,
        kernel_size: int = 3,
        dropout_rate: float = 0.5,
        flows: int = 4,
        dds_conv_layers: int = 3,
        global_channels: int = -1,
    ):
        """Initialize StochasticDurationPredictor module.

        Args:
            channels (int): Number of channels.
            kernel_size (int): Kernel size.
            dropout_rate (float): Dropout rate.
            flows (int): Number of flows.
            dds_conv_layers (int): Number of conv layers in DDS conv.
            global_channels (int): Number of global conditioning channels.

        """
        super().__init__()

        self.pre = torch.nn.Conv1d(channels, channels, 1)
        self.dds = DilatedDepthSeparableConv(
            channels,
            kernel_size,
            layers=dds_conv_layers,
            dropout_rate=dropout_rate,
        )
        self.proj = torch.nn.Conv1d(channels, channels, 1)

        self.log_flow = LogFlow()
        self.flows = torch.nn.ModuleList()
        self.flows += [ElementwiseAffineFlow(2)]
        for i in range(flows):
            self.flows += [
                ConvFlow(
                    2,
                    channels,
                    kernel_size,
                    layers=dds_conv_layers,
                )
            ]
            self.flows += [FlipFlow()]

        self.post_pre = torch.nn.Conv1d(1, channels, 1)
        self.post_dds = DilatedDepthSeparableConv(
            channels,
            kernel_size,
            layers=dds_conv_layers,
            dropout_rate=dropout_rate,
        )
        self.post_proj = torch.nn.Conv1d(channels, channels, 1)
        self.post_flows = torch.nn.ModuleList()
        self.post_flows += [ElementwiseAffineFlow(2)]
        for i in range(flows):
            self.post_flows += [
                ConvFlow(
                    2,
                    channels,
                    kernel_size,
                    layers=dds_conv_layers,
                )
            ]
            self.post_flows += [FlipFlow()]

        if global_channels > 0:
            self.global_conv = torch.nn.Conv1d(global_channels, channels, 1)

    def forward(
        self,
        x: torch.Tensor,
        x_mask: torch.Tensor,
        w: Optional[torch.Tensor] = None,
        g: Optional[torch.Tensor] = None,
        inverse: bool = False,
        noise_scale: float = 1.0,
    ) -> torch.Tensor:
        """Calculate forward propagation.

        Args:
            x (Tensor): Input tensor (B, channels, T_text).
            x_mask (Tensor): Mask tensor (B, 1, T_text).
            w (Optional[Tensor]): Duration tensor (B, 1, T_text).
            g (Optional[Tensor]): Global conditioning tensor (B, channels, 1)
            inverse (bool): Whether to inverse the flow.
            noise_scale (float): Noise scale value.

        Returns:
            Tensor: If not inverse, negative log-likelihood (NLL) tensor (B,).
                If inverse, log-duration tensor (B, 1, T_text).

        """
        x = x.detach()  # stop gradient
        x = self.pre(x)
        if g is not None:
            x = x + self.global_conv(g.detach())  # stop gradient
        x = self.dds(x, x_mask)
        x = self.proj(x) * x_mask

        if not inverse:
            assert w is not None, "w must be provided."
            h_w = self.post_pre(w)
            h_w = self.post_dds(h_w, x_mask)
            h_w = self.post_proj(h_w) * x_mask
            e_q = (
                torch.randn(
                    w.size(0),
                    2,
                    w.size(2),
                ).to(device=x.device, dtype=x.dtype)
                * x_mask
            )
            z_q = e_q
            logdet_tot_q = 0.0
            for flow in self.post_flows:
                z_q, logdet_q = flow(z_q, x_mask, g=(x + h_w))
                logdet_tot_q += logdet_q
            z_u, z1 = torch.split(z_q, [1, 1], 1)
            u = torch.sigmoid(z_u) * x_mask
            z0 = (w - u) * x_mask
            logdet_tot_q += torch.sum(
                (F.logsigmoid(z_u) + F.logsigmoid(-z_u)) * x_mask, [1, 2]
            )
            logq = (
                torch.sum(-0.5 * (math.log(2 * math.pi) + (e_q**2)) * x_mask, [1, 2])
                - logdet_tot_q
            )

            logdet_tot = 0
            z0, logdet = self.log_flow(z0, x_mask)
            logdet_tot += logdet
            z = torch.cat([z0, z1], 1)
            for flow in self.flows:
                z, logdet = flow(z, x_mask, g=x, inverse=inverse)
                logdet_tot = logdet_tot + logdet
            nll = (
                torch.sum(0.5 * (math.log(2 * math.pi) + (z**2)) * x_mask, [1, 2])
                - logdet_tot
            )
            return nll + logq  # (B,)
        else:
            flows = list(reversed(self.flows))
            flows = flows[:-2] + [flows[-1]]  # remove a useless vflow
            z = (
                torch.randn(
                    x.size(0),
                    2,
                    x.size(2),
                ).to(device=x.device, dtype=x.dtype)
                * noise_scale
            )
            for flow in flows:
                z = flow(z, x_mask, g=x, inverse=inverse)
            z0, z1 = z.split(1, 1)
            logw = z0
            return logw


class LayerNorm(torch.nn.Module):
    def __init__(self, channels, eps=1e-5):
        super().__init__()
        self.channels = channels
        self.eps = eps

        self.gamma = torch.nn.Parameter(torch.ones(channels))
        self.beta = torch.nn.Parameter(torch.zeros(channels))

    def forward(self, x):
        x = x.transpose(1, -1)
        x = F.layer_norm(x, (self.channels,), self.gamma, self.beta, self.eps)
        return x.transpose(1, -1)


class DurationDiscriminator(torch.nn.Module):  # vits2
    # TODO : not using "spk conditioning" for now according to the paper.
    # Can be a better discriminator if we use it.
    def __init__(
        self, in_channels, filter_channels, kernel_size, p_dropout, gin_channels=0
    ):
        super().__init__()

        self.in_channels = in_channels
        self.filter_channels = filter_channels
        self.kernel_size = kernel_size
        self.p_dropout = p_dropout
        self.gin_channels = gin_channels

        self.conv_1 = torch.nn.Conv1d(
            in_channels, filter_channels, kernel_size, padding=kernel_size // 2
        )
        self.norm_1 = LayerNorm(filter_channels)
        self.conv_2 = torch.nn.Conv1d(
            filter_channels, filter_channels, kernel_size, padding=kernel_size // 2
        )
        self.norm_2 = LayerNorm(filter_channels)
        self.dur_proj = torch.nn.Conv1d(1, filter_channels, 1)

        self.pre_out_conv_1 = torch.nn.Conv1d(
            2 * filter_channels, filter_channels, kernel_size, padding=kernel_size // 2
        )
        self.pre_out_norm_1 = LayerNorm(filter_channels)
        self.pre_out_conv_2 = torch.nn.Conv1d(
            filter_channels, filter_channels, kernel_size, padding=kernel_size // 2
        )
        self.pre_out_norm_2 = LayerNorm(filter_channels)

        self.output_layer = torch.nn.Sequential(
            torch.nn.Linear(filter_channels, 1), torch.nn.Sigmoid()
        )

    def forward_probability(self, x, x_mask, dur):
        dur = self.dur_proj(dur)
        x = torch.cat([x, dur], dim=1)
        x = self.pre_out_conv_1(x * x_mask)
        x = torch.relu(x)
        x = self.pre_out_norm_1(x)
        x = self.pre_out_conv_2(x * x_mask)
        x = torch.relu(x)
        x = self.pre_out_norm_2(x)
        x = x * x_mask
        x = x.transpose(1, 2)
        output_prob = self.output_layer(x)
        return output_prob

    def forward(self, x, x_mask, dur_r, dur_hat):
        x = torch.detach(x)

        x = self.conv_1(x * x_mask)
        x = torch.relu(x)
        x = self.norm_1(x)
        x = self.conv_2(x * x_mask)
        x = torch.relu(x)
        x = self.norm_2(x)

        output_probs = []
        for dur in [dur_r, dur_hat]:
            output_prob = self.forward_probability(x, x_mask, dur)
            output_probs.append([output_prob])

        return output_probs
