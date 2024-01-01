""" Official Implementation
Resource-Adaptive Foundation Models (RAFM) Fine-tuning: Single Fine-Tuning Meets Varied Resource Constraints"
"""

import os
from typing import Any
import torch
from torch import nn
from .model_downsize import (
    bert_module_handler,
    arc_config_sampler,
    vit_module_handler,
    sam_module_handler,
)
from .param_prioritization import *
from .utils import calculate_params, save_dict_to_file, load_dict_from_file


class RAFM:
    def __init__(self, model, elastic_config=None) -> None:
        self.model = model
        self.total_params = calculate_params(model=model)

        if not elastic_config:
            # set defalt search space configuration (this is defalt setting for bert)
            elastic_config = {
                "atten_out_space": [768],
                "inter_hidden_space": [3072, 1920, 1280],
                "residual_hidden_space": [768],
            }
            print(
                f"[Warning]: No elastic configuration provides. Set to the defalt elastic space {elastic_config}."
            )
        elif isinstance(elastic_config, str):
            elastic_config = load_dict_from_file(elastic_config)

        assert isinstance(
            elastic_config, dict
        ), "Invalid elastic_config, expect input a dictionary or file path"

        self.elastic_config = elastic_config
        self.local_grads = []
        self.alphas = []
    def random_resource_aware_model(self):
        """_summary_

        Raises:
            NotImplementedError: _description_

        Returns:
            _type_: _description_
        """

        if "bert" == self.model.config.model_type.lower():
            arc_config = arc_config_sampler(
                **self.elastic_config, n_layer=self.model.config.num_hidden_layers
            )
            subnetwork, total_params = bert_module_handler(self.model, arc_config)
        elif "vit" == self.model.config.model_type.lower():
            arc_config = arc_config_sampler(
                **self.elastic_config, n_layer=self.model.config.num_hidden_layers
            )
            subnetwork, total_params = vit_module_handler(self.model, arc_config)
        elif "sam" == self.model.config.model_type.lower():
            arc_config = arc_config_sampler(
                **self.elastic_config,
                n_layer=self.model.vision_encoder.config.num_hidden_layers,
            )
            subnetwork, total_params = sam_module_handler(self.model, arc_config)
        else:
            raise NotImplementedError
        return subnetwork, total_params, arc_config

    def smallest_model(self):
        """Return the smallest model in the elastic space

        Returns:
            - subnetwork (nn.Module): The smallest model in the elastic space
            - params (int): The number of parameters in million of the smallest model
            - arc_config (dict): The configuration of the smallest model
        """
        arc_config = arc_config_sampler(**self.elastic_config, smallest=True)
        subnetwork, params = self.resource_aware_model(arc_config)
        return subnetwork, params, arc_config

    def resource_aware_model(self, arc_config):
        if "bert" == self.model.config.model_type.lower():
            return bert_module_handler(self.model, arc_config)
        elif "vit" == self.model.config.model_type.lower():
            return vit_module_handler(self.model, arc_config)
        elif "sam" == self.model.config.model_type.lower():
            return sam_module_handler(self.model, arc_config)
        else:
            raise NotImplementedError

    def salient_parameter_prioritization(self, metric=l1_norm):
        self.model = salient_parameter_prioritization(self.model, metric)
    
    def grad_accumulate(self, local_grad):
        self.local_grads.append(local_grad)
        
    
    def apply_grad(self):
        
        self.model.to("cpu")
        with torch.no_grad():
            for name, param in self.model.named_parameters():
                param *= 0
                for local_grad in self.local_grads:
                    local_param = local_grad.state_dict()[name].cpu()
                    if len(local_param.shape) == 2:
                        param[
                            : local_param.shape[0], : local_param.shape[1]
                        ] += local_param / len(self.local_grads)
                    else:
                        param[: local_param.shape[0]] += local_param / len(
                            self.local_grads
                        )
        self.local_grads.clear()
    
    def grad_aggregate(self, local_grads:list[nn.Module]):
        """Aggregate downscaled model gradients via weihted average

        Args:
            local_grads (list[nn.Module]): Downscaled model gradients
        """
        self.model.to("cpu")
        with torch.no_grad():
            for name, param in self.model.named_parameters():
                param *= 0
                for local_grad in local_grads:
                    local_param = local_grad.state_dict()[name].cpu()
                    if len(local_param.shape) == 2:
                        param[
                            : local_param.shape[0], : local_param.shape[1]
                        ] += local_param / len(local_grads)
                    else:
                        param[: local_param.shape[0]] += local_param / len(
                            local_grads
                        )
    def grad_normalization(self):
        """Normalize the gradients via previous epoch's gradients"""
        pass
            
    def save_ckpt(self, dir):
        self.model.save_pretrained(os.path.join(dir))
        save_dict_to_file(self.elastic_config, os.path.join(dir, "elastic_space.json"))

    def load_ckpt(self, dir):
        self.model = self.model.from_pretrained(dir)

        if os.path.exists(os.path.join(dir, "elastic_space.json")):
            self.elastic_config = load_dict_from_file(
                os.path.join(dir, "elastic_space.json")
            )