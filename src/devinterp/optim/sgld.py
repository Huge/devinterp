from typing import Union, Callable
import warnings

import torch


class SGLD(torch.optim.Optimizer):
    r"""
    Implements Stochastic Gradient Langevin Dynamics (SGLD) optimizer.
    
    This optimizer blends Stochastic Gradient Descent (SGD) with Langevin Dynamics,
    introducing Gaussian noise to the gradient updates. It can also include a
    localization term that acts like a special form of weight decay.

    It follows Lau et al.'s (2023) implementation, which is a modification of 
    Welling and Teh (2011) that omits the learning rate schedule and introduces 
    a localization term that pulls the weights towards their initial values.

    The equation for the update is as follows:

    $$
    \begin{gathered}
    \Delta w_t=\frac{\epsilon}{2}\left(\frac{\beta n}{m} \sum_{i=1}^m \nabla \log p\left(y_{l_i} \mid x_{l_i}, w_t\right)+\gamma\left(w^_0-w_t\right) - \lambda w_t\right) \\
    +N(0, \epsilon\sigma^2)
    \end{gathered}
    $$

    where $w_t$ is the weight at time $t$, $\epsilon$ is the learning rate, 
    $(\beta n)$ is the inverse temperature (we're in the tempered Bayes paradigm), 
    $n$ is the number of training samples, $m$ is the batch size, $\gamma$ is 
    the localization strength, $\lambda$ is the weight decay strength,
    and $\sigma$ is the noise term.

    :param params: Iterable of parameters to optimize or dicts defining parameter groups (required)
    :param lr: Learning rate 
    :param noise_level: Amount of Gaussian noise introduced into gradient updates (default: 1).
    :param weight_decay: L2 regularization term, applied as weight decay (default: 0)
    :param localization: Strength of the force pulling weights back to their initial values (default: 0)
    :param temperature: Temperature, float (default: 1., set by sample() to utils.optimal_temperature(dataloader)=len(batch_size)/np.log(len(batch_size)))
    :param bounding_box_size: the size of the bounding box enclosing our trajectory (default: 0)
    :param save_noise: whether to store the per-parameter noise during optimization (default: False)

    Example:
        >>> optimizer = SGLD(model.parameters(), lr=0.1, temperature=torch.log(n)/n)
        >>> optimizer.zero_grad()
        >>> loss_fn(model(input), target).backward()
        >>> optimizer.step()

    Note:
        - The `localization` term is unique to this implementation and serves to guide the
        weights towards their original values. This is useful for estimating quantities over the local 
        posterior.
        - The `noise_level` is not intended to be changed, except when testing! Doing so will raise a warning.
    """

    def __init__(
        self,
        params,
        lr=0.01,
        noise_level=1.0,
        weight_decay=0.0,
        localization=0.0, 
        temperature: Union[Callable, float] = 1.0,
        bounding_box_size=None,
        save_noise=False,
    ):
        if noise_level != 1.0:
            warnings.warn(
                "Warning: noise_level in SGLD is unequal to one, this removes SGLD posterior sampling guarantees."
            )
        if temperature == 1.0:
            warnings.warn(
                "Warning: temperature set to 1, LLC estimates will be off unless you know what you're doing. Use utils.optimal_temperature(dataloader) instead"
            )
        defaults = dict(
            lr=lr,
            noise_level=noise_level,
            weight_decay=weight_decay,
            localization=localization,
            temperature=temperature,
            bounding_box_size=bounding_box_size,
        )
        super(SGLD, self).__init__(params, defaults)
        self.save_noise = save_noise
        self.noise = None

        # Save the initial parameters if the localization term is set
        for group in self.param_groups:
            if group["localization"] != 0 or group["bounding_box_size"] != 0:
                for p in group["params"]:
                    param_state = self.state[p]
                    param_state["initial_param"] = p.data.clone().detach()

    def step(self, closure=None):
        self.noise = []
        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue
                param_state = self.state[p]
                dw = p.grad.data * group["temperature"]

                if group["weight_decay"] != 0:
                    dw.add_(p.data, alpha=group["weight_decay"])

                if group["localization"] != 0:
                    initial_param = self.state[p]["initial_param"]
                    dw.add_((p.data - initial_param), alpha=group["localization"])

                p.data.add_(dw, alpha=-0.5 * group["lr"])

                # Add Gaussian noise
                noise = torch.normal(
                    mean=0.0, std=group["noise_level"], size=dw.size(), device=dw.device
                )
                if self.save_noise:
                    self.noise.append(noise)
                p.data.add_(noise, alpha=group["lr"] ** 0.5)

                # Rebound if exceeded bounding box size
                if group["bounding_box_size"]:
                    torch.clamp_(
                        p.data,
                        min=param_state["initial_param"] - group["bounding_box_size"],
                        max=param_state["initial_param"] + group["bounding_box_size"],
                    )
