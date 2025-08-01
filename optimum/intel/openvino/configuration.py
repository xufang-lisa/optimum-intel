#  Copyright 2022 The HuggingFace Team. All rights reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
import copy
import dataclasses
import inspect
import json
import logging
from dataclasses import dataclass
from enum import Enum
from functools import reduce
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union

import torch
from transformers.utils.quantization_config import QuantizationConfigMixin

from optimum.configuration_utils import BaseConfig

from ..utils.import_utils import is_nncf_available, is_nncf_version
from .utils import (
    PREDEFINED_CAUSAL_LANGUAGE_DATASETS,
    PREDEFINED_LANGUAGE_DATASETS,
    PREDEFINED_SAM_DATASETS,
    PREDEFINED_SD_DATASETS,
    PREDEFINED_SPEECH_TO_TEXT_DATASETS,
    PREDEFINED_VISUAL_LM_DATASETS,
)


if is_nncf_available():
    import nncf

logger = logging.getLogger(__name__)


class OVQuantizationMethod(str, Enum):
    DEFAULT = "default"
    HYBRID = "hybrid"
    AWQ = "awq"


# Default configs for 4-bit weight quantization
_DEFAULT_4BIT_WQ_CONFIGS = {
    "databricks/dolly-v2-3b": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "EleutherAI/gpt-j-6b": {"bits": 4, "sym": False, "group_size": 64},
    "facebook/opt-6.7b": {"bits": 4, "sym": False, "group_size": 64, "ratio": 0.8},
    "togethercomputer/RedPajama-INCITE-7B-Instruct": {"bits": 4, "sym": False, "group_size": 128},
    "HuggingFaceH4/zephyr-7b-beta": {
        "bits": 4,
        "sym": True,
        "group_size": 128,
        "ratio": 0.8,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
    },
    "meta-llama/Llama-2-7b-hf": {"bits": 4, "sym": True, "group_size": 128, "ratio": 0.6},
    "meta-llama/Llama-2-7b-chat-hf": {
        "bits": 4,
        "sym": True,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "meta-llama/Llama-2-13b-chat-hf": {"bits": 4, "sym": True, "group_size": 64, "ratio": 0.8},
    "stabilityai/stablelm-3b-4e1t": {
        "bits": 4,
        "sym": True,
        "group_size": 64,
        "ratio": 0.8,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
    },
    "stabilityai/stablelm-zephyr-3b": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 0.9,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "stabilityai/stable-code-3b": {"bits": 4, "sym": True, "group_size": 64, "ratio": 0.8},
    "pansophic/rocket-3B": {"bits": 4, "sym": True, "group_size": 128, "ratio": 0.8},
    "THUDM/chatglm2-6b": {"bits": 4, "sym": True, "group_size": 128, "ratio": 0.72},
    "Qwen/Qwen-7B-Chat": {"bits": 4, "sym": True, "group_size": 128, "ratio": 0.6},
    "Qwen/Qwen2.5-1.5B-Instruct": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 0.9,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "Qwen/Qwen2.5-7B-Instruct": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "Qwen/Qwen2.5-Coder-3B-Instruct": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "Qwen/Qwen3-1.7B": {
        "bits": 4,
        "sym": True,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "Qwen/Qwen3-4B": {
        "bits": 4,
        "sym": True,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "Qwen/Qwen3-8B": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "openlm-research/open_llama_3b": {"bits": 4, "sym": False, "group_size": 64, "all_layers": True},
    "openlm-research/open_llama_3b_v2": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
    },
    "tiiuae/falcon-7b-instruct": {"bits": 4, "sym": False, "group_size": 64},
    "psmathur/orca_mini_3b": {
        "bits": 4,
        "sym": True,
        "group_size": 64,
        "all_layers": True,
    },
    "bigscience/bloomz-560m": {
        "bits": 4,
        "sym": True,
        "group_size": 64,
        "ratio": 0.8,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
    },
    "mistralai/Mixtral-8x7B-v0.1": {"bits": 4, "sym": True, "group_size": 128, "ratio": 0.8},
    "facebook/opt-2.7b": {"bits": 4, "sym": True, "group_size": 128, "ratio": 0.7},
    "togethercomputer/RedPajama-INCITE-Chat-3B-v1": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "lmsys/vicuna-7b-v1.5": {"bits": 4, "sym": False, "group_size": 128, "ratio": 1.0},
    "stabilityai/stablelm-tuned-alpha-3b": {"bits": 4, "sym": False, "group_size": 128, "ratio": 0.8},
    "mistralai/Mistral-7B-v0.1": {"bits": 4, "sym": True, "group_size": 128, "ratio": 0.9},
    "baichuan-inc/Baichuan2-7B-Chat": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 0.8,
    },
    "baichuan-inc/Baichuan2-13B-Chat": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "lmsys/longchat-7b-16k": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "bigcode/starcoder2-3b": {"bits": 4, "sym": False, "group_size": 128, "ratio": 0.9},
    "bigcode/starcoder2-15b": {"bits": 4, "sym": False, "group_size": 64, "ratio": 1.0},
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "microsoft/phi-2": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "stabilityai/stablelm-tuned-alpha-7b": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "meta-llama/Meta-Llama-3.1-8B-Instruct": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 0.8,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "meta-llama/Llama-3.2-1B-Instruct": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "meta-llama/Meta-Llama-3.1-8B": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 0.8,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "microsoft/Phi-3-mini-4k-instruct": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "scale_estimation": True,
    },
    "microsoft/Phi-3.5-mini-instruct": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "microsoft/Phi-4-mini-instruct": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B": {
        "bits": 4,
        "sym": False,
        "group_size": 32,
        "ratio": 0.7,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B": {
        "bits": 4,
        "sym": False,
        "group_size": 128,
        "ratio": 1.0,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
        "scale_estimation": True,
    },
    "deepseek-ai/DeepSeek-R1-Distill-Llama-8B": {
        "bits": 4,
        "sym": False,
        "group_size": 64,
        "ratio": 0.8,
        "dataset": "wikitext2",
        "quant_method": OVQuantizationMethod.AWQ,
    },
    "microsoft/Phi-4-multimodal-instruct": {
        "quantization_configs": {
            "lm_model": {
                "bits": 4,
                "sym": False,
                "group_size": 64,
                "dataset": "contextual",
                "quant_method": OVQuantizationMethod.AWQ,
                "scale_estimation": True,
                "ignored_scope": {
                    "patterns": [
                        "__module\\.model\\.layers\\.\\d+\\.(mlp\\.(gate_up_proj|down_proj)|self_attn\\.(qkv_proj|o_proj))\\.lora_B\\.speech/ov_ext::linear/MatMul",
                    ]
                },
            },
            "text_embeddings_model": {"bits": 8, "sym": True, "weight_only": True},
            "audio_encoder_model": {"bits": 8, "sym": True, "weight_only": True},
            "vision_embeddings_model": {"bits": 8, "sym": True, "weight_only": True},
        },
    },
}

# Add configs for model id aliases
# The list below contains pairs of model ids: config for the second model id will be copied from the first model id.
model_id_aliases = [
    ("meta-llama/Meta-Llama-3.1-8B-Instruct", "meta-llama/Llama-3.1-8B-Instruct"),
    ("meta-llama/Meta-Llama-3.1-8B", "meta-llama/Llama-3.1-8B"),
]
for m_id_1, m_id_2 in model_id_aliases:
    _DEFAULT_4BIT_WQ_CONFIGS[m_id_2] = _DEFAULT_4BIT_WQ_CONFIGS[m_id_1]

_DEFAULT_4BIT_WQ_CONFIG = {
    "bits": 4,
    "ratio": 1.0,
    "sym": False,
    "group_size": 128,
    "all_layers": None,
}


# Default configs for int8 full quantization
_DEFAULT_INT8_FQ_CONFIGS = {
    "facebook/sam-vit-base": {
        "quantization_configs": {
            "vision_encoder": {
                "dtype": "int8",
                "dataset": "coco",
                "num_samples": 128,
                "weight_only": False,
            },
        }
    },
    "facebook/sam-vit-large": {
        "quantization_configs": {
            "vision_encoder": {
                "dtype": "int8",
                "dataset": "coco",
                "num_samples": 128,
                "weight_only": False,
            },
        }
    },
    "facebook/sam-vit-huge": {
        "quantization_configs": {
            "vision_encoder": {
                "dtype": "int8",
                "dataset": "coco",
                "num_samples": 128,
                "weight_only": False,
            },
        }
    },
    "google-t5/t5-small": {
        "dtype": "int8",
        "dataset": "wikitext2",
        "num_samples": 300,
        "smooth_quant_alpha": -1,
    },
    "google-t5/t5-large": {
        "dtype": "int8",
        "dataset": "wikitext2",
        "num_samples": 300,
        "smooth_quant_alpha": -1,
    },
    "google-t5/t5-3b": {
        "dtype": "int8",
        "dataset": "wikitext2",
        "num_samples": 300,
        "smooth_quant_alpha": -1,
    },
    "FacebookAI/roberta-large": {
        "dtype": "int8",
        "dataset": "wikitext2",
        "num_samples": 128,
        "smooth_quant_alpha": 0.5,
    },
    "google-bert/bert-base-multilingual-uncased": {
        "dtype": "int8",
        "dataset": "wikitext2",
        "num_samples": 300,
    },
    "openai/clip-vit-base-patch16": {
        "dtype": "int8",
        "dataset": "conceptual_captions",
        "num_samples": 300,
        "smooth_quant_alpha": 0.6,
    },
    "openai/clip-vit-base-patch32": {
        "dtype": "int8",
        "dataset": "conceptual_captions",
        "num_samples": 300,
        "smooth_quant_alpha": 0.6,
    },
    "openai/clip-vit-large-patch14": {
        "dtype": "int8",
        "dataset": "conceptual_captions",
        "num_samples": 300,
        "smooth_quant_alpha": 0.6,
    },
    "hf-tiny-model-private/tiny-random-CLIPModel": {  # For test purposes
        "dtype": "int8",
        "dataset": "conceptual_captions",
        "num_samples": 1,
        "smooth_quant_alpha": 0.9,
    },
}


def get_default_int4_config(model_id_or_path: str):
    """
    Args:
        model_id_or_path (`str`):
            id of the model or path to it.
    Returns:
        Default int4 config for the given model or generic default int4 config.
    """
    logger.warning(
        "The `get_default_int4_config` function is deprecated and will be removed in optimum-intel v1.25.0. "
        "Please use `get_default_quantization_config` instead."
    )

    return get_default_quantization_config(model_id_or_path, "int4") or _DEFAULT_4BIT_WQ_CONFIG


def get_default_quantization_config(
    model_id_or_path: str, weight_format: Optional[str] = None, quant_mode: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Args:
        model_id_or_path (`str`):
            id of the model or path to it.
        weight_format (`str`, *optional*):
            The format of the weights. Currently only "int4" value is supported.
        quant_mode (`str`, *optional*):
            The quantization mode. Currently only "int8" value is supported.
    Returns:
        Default quantization config for the given model if found and `None` otherwise.
    """

    if weight_format is None and quant_mode is None:
        raise ValueError("Either `weight_format` or `quant_mode` must be provided.")

    if weight_format == "int4":
        default_configs_dict = _DEFAULT_4BIT_WQ_CONFIGS
    elif quant_mode == "int8":
        default_configs_dict = _DEFAULT_INT8_FQ_CONFIGS
    else:
        return None

    # Check if the model_id_or_path is in the default configs
    if model_id_or_path in default_configs_dict:
        return default_configs_dict[model_id_or_path]

    # Try to match by config.json
    model_path = Path(model_id_or_path)
    config_path = model_path / "config.json"
    if config_path.exists():
        with config_path.open("r") as config_f:
            config = json.load(config_f)
            original_model_name = config.get("_name_or_path", "")
        if original_model_name in default_configs_dict:
            return default_configs_dict[original_model_name]

    # Try to match by folder name
    for model_id, config in default_configs_dict.items():
        short_id = model_id.split("/")[-1]
        if model_path.name == short_id:
            return config

    return None


@dataclass
class OVQuantizationConfigBase(QuantizationConfigMixin):
    """
    Base configuration class for quantization parameters
    """

    quant_method = OVQuantizationMethod.DEFAULT

    def __init__(
        self,
        ignored_scope: Optional[Union[dict, "nncf.IgnoredScope"]] = None,
        num_samples: Optional[int] = None,
        dataset: Optional[Union[str, List[str]]] = None,
        tokenizer: Optional[str] = None,
        processor: Optional[str] = None,
        trust_remote_code: Optional[bool] = False,
        **kwargs,
    ):
        """
        Args:
            ignored_scope (`dict` or `nncf.IgnoredScope`, *optional*):
                An ignored scope that defines the list of model nodes to be ignored during quantization. Dictionary
                entries provided via this argument are used to create an instance of `nncf.IgnoredScope` class.
            num_samples (`int`, *optional*):
                The maximum number of samples composing the calibration dataset.
            dataset (`str or List[str]`, *optional*):
                The dataset used for data-aware optimization with NNCF.
            tokenizer (`str`, *optional*):
                The tokenizer used to process the dataset.
            processor (`str`, *optional*):
                A transformers processor used to process the dataset inputs.
            trust_remote_code (`bool`, defaults to `False`):
                Allows to use custom code for the modeling hosted in the model repository. This option should only be
                set for repositories you trust and in which you have read the code, as it will execute on your local
                machine arbitrary code present in the model repository.
        """
        self.num_samples = num_samples
        self.dataset = dataset
        self.tokenizer = tokenizer
        self.processor = processor
        self.trust_remote_code = trust_remote_code
        if isinstance(ignored_scope, nncf.IgnoredScope):
            ignored_scope = ignored_scope.__dict__
        self.ignored_scope = ignored_scope
        self.kwargs = kwargs

    def post_init(self):
        try:
            self.get_ignored_scope_instance()
        except Exception as e:
            raise ValueError(
                f"Can't create an `IgnoredScope` object from the provided ignored scope dict: {self.ignored_scope}.\n{e}"
            )
        if not (self.num_samples is None or isinstance(self.num_samples, int) and self.num_samples > 0):
            raise ValueError(f"`num_samples` is expected to be a positive integer, but found: {self.num_samples}")

    def get_ignored_scope_instance(self) -> "nncf.IgnoredScope":
        if self.ignored_scope is None:
            return nncf.IgnoredScope()
        return nncf.IgnoredScope(**copy.deepcopy(self.ignored_scope))

    def clone(self):
        return copy.deepcopy(self)

    def to_dict(self) -> Dict[str, Any]:
        # Unpack kwargs dict
        result = super().to_dict()
        result = result | result.pop("kwargs", {})
        self._dataclasses_to_dict(result)
        return result

    @staticmethod
    def _dataclasses_to_dict(d: dict):
        """
        Search for dataclasses in the dict and convert them to dicts.
        """
        for k in d:
            v = d[k]
            if isinstance(v, dict):
                OVQuantizationConfigBase._dataclasses_to_dict(v)
            elif dataclasses.is_dataclass(v):
                d[k] = dataclasses.asdict(v)


@dataclass
class OVWeightQuantizationConfig(OVQuantizationConfigBase):
    """
    This is a wrapper class about all possible attributes and features that you can play with a model that has been
    loaded using `optimum-intel` api for weight-only quantization with NNCF. For full model quantization please see
    OVQuantizationConfig.
    Args:
        bits (`int`, defaults to 8):
            The number of bits to quantize to.
        sym (`bool`, defaults to `False`):
            Whether to use symmetric quantization on the weights. Does not affect backup precision symmetricity.
        group_size (`int`, *optional*):
            The group size to use for quantization. Recommended value is 128 and -1 uses per-column quantization.
        tokenizer (`str`, *optional*):
            The tokenizer used to process the dataset. You can pass either:
                - A string, the *model id* of a predefined tokenizer hosted inside a model repo on huggingface.co.
                    Valid model ids can be located at the root-level, like `bert-base-uncased`, or namespaced under a
                    user or organization name, like `dbmdz/bert-base-german-cased`.
                - A path to a *directory* containing vocabulary files required by the tokenizer, for instance saved
                    using the [`~PreTrainedTokenizer.save_pretrained`] method, e.g., `./my_model_directory/`.
        trust_remote_code (`bool`, defaults to `False`):
            Allows to use custom code for the modeling hosted in the model repository. This option should only be set
            for repositories you trust and in which you have read the code, as it will execute on your local machine
            arbitrary code present in the model repository.
        dataset (`str or List[str]`, *optional*):
            The dataset used for data-aware compression with NNCF.
            - For language models you can provide your own dataset in a list of strings or just use one from the list
                ['auto', 'wikitext2','c4','c4-new']. With 'auto' the dataset will be collected from model's generations.
            - For diffusion models the dataset must be one of ['conceptual_captions',
                'laion/220k-GPT4Vision-captions-from-LIVIS', 'laion/filtered-wit'].
            - For visual language models the dataset must be set to 'contextual'.
            Alternatively, you can provide data objects via `calibration_dataset` argument of `OVQuantizer.quantize()`
            method.
        ratio (`float`, defaults to 1.0):
            The ratio between baseline and backup precisions (e.g. 0.9 means 90% of layers quantized to INT4_ASYM
            and the rest to INT8_ASYM).
            Note: If dataset is provided, and the ratio is less than 1.0, then data-aware mixed precision assignment
            will be applied.
        all_layers (`bool`, *optional*):
            Defines how many layers are compressed to 4-bits while the rest are kept in 8-bit precision.
        sensitivity_metric (`str`, *optional*):
            The sensitivity metric for assigning quantization precision to layers. In order to
            preserve the accuracy of the model, the more sensitive layers receives a higher precision.
        ignored_scope (`dict` or `nncf.IgnoredScope`, *optional*):
            An ignored scope that defines the list of model nodes to be ignored during quantization. Dictionary
            entries provided via this argument are used to create an instance of `nncf.IgnoredScope` class.
        num_samples (`int`, *optional*):
            The maximum number of samples composing the calibration dataset.
        quant_method (`str or OVQuantizationMethod`, defaults of OVQuantizationMethod.DEFAULT):
            Weight compression method to apply. Possible options:
                - "default": default weight quantization will be applied.
                - "awq": compressed weights will be computed according to the Activation-Aware-Quantization (AWQ)
                  method. AWQ improves generation quality of INT4-compressed LLMs. If dataset is provided, a data-aware
                  activation-based version of the algorithm will be executed, which requires additional time. Otherwise,
                  data-free AWQ will be applied which relies on per-column magnitudes of weights instead of activations.
                  Note: it's possible that there will be no matching patterns in the model to apply AWQ, in such case it
                  will be skipped.
                - "hybrid": The hybrid mode involves the quantization of weights in MatMul and Embedding layers, and
                  activations of other layers, facilitating accuracy preservation post-optimization while reducing
                  the model size. Hybrid mode performs well when applied to a UNet model in diffusion pipelines.
        scale_estimation (`bool`, *optional*):
            Indicates whether to apply a scale estimation algorithm that minimizes the L2 error between the original and
            compressed layers. Providing a dataset is required to run scale estimation.
        dtype (`str`, *optional*):
            Data type weights are compressed to. Possible values: ['int4', 'int8', 'mxfp4', 'nf4', 'cb4'].
            Option 'cb4' represents a codebook with 16 fixed fp8 values in E4M3 format.
        qptq (`bool`, *optional*):
            Whether to apply GPTQ algorithm. GPTQ optimizes compressed weights in a layer-wise fashion to minimize the
            difference between activations of a compressed and original layer. Dataset is required to run GPTQ.
        processor (`str`, *optional*):
            A transformers processor used to process inputs for multi-modal models. You can pass either:
                - A string, the *model id* of a predefined processor hosted inside a model repo on huggingface.co.
                - A path to a *directory* containing files required by the processor, for instance saved
                    using the [`~AutoProcessor.save_pretrained`] method, e.g., `./my_model_directory/`.
        lora_correction (`bool`, *optional*):
            If True, apply LoRA Correction algorithm. When enabled, this algorithm introduces low-rank adaptation
            layers in the model that can recover accuracy after weight compression at some cost of inference latency.
            It calculates low-rank matrices via singular value decomposition (SVD) on the difference between the
            original and quantized weights. These matrices are iteratively refined by solving a system of linear
            equations to improve accuracy.
        backup_precision (`str`, defaults to None):
            Defines a backup precision for mixed-precision weight compression.
            - "none" stands for original floating-point precision of the model weights, in this case weights are
                retained in their original precision without any quantization.
            - "int8_sym" stands for 8-bit integer symmetric quantization without zero point.
            - "int8_asym" stands for 8-bit integer asymmetric quantization with zero points per each quantization group.
        statistics_path (`str`, *optional*):
            Directory path to dump/load data-aware statistics. This is useful when running data-aware quantization
            multiple times on the same model and dataset to avoid recomputing statistics.
            Please note that the statistics depend on the dataset, so if you change the dataset, you should also change
            the statistics path to avoid confusion.
        kwargs: Additional parameters for nncf.compress_weights() call.
    """

    def __init__(
        self,
        bits: int = 8,
        sym: bool = False,
        group_size: Optional[int] = None,
        tokenizer: Optional[str] = None,
        trust_remote_code: bool = False,
        dataset: Optional[Union[str, List[str]]] = None,
        ratio: float = 1.0,
        all_layers: Optional[bool] = None,
        sensitivity_metric: Optional[str] = None,
        ignored_scope: Optional[Union[dict, "nncf.IgnoredScope"]] = None,
        num_samples: Optional[int] = None,
        quant_method: Union[str, OVQuantizationMethod] = OVQuantizationMethod.DEFAULT,
        scale_estimation: bool = None,
        dtype: Optional[str] = None,
        gptq: bool = None,
        processor: Optional[str] = None,
        lora_correction: bool = None,
        backup_precision: Optional[str] = None,
        statistics_path: Optional[str] = None,
        **kwargs,
    ):
        weight_format = kwargs.pop("weight_format", None)
        if weight_format is not None:
            logger.warning(
                "The `weight_format` parameter is deprecated and will be removed in optimum-intel v1.24.0. "
                "Please use `dtype` instead."
            )
            dtype = weight_format
        super().__init__(
            ignored_scope=ignored_scope,
            num_samples=num_samples,
            dataset=dataset,
            tokenizer=tokenizer,
            processor=processor,
            trust_remote_code=trust_remote_code,
            **kwargs,
        )
        self.bits = bits
        self.sym = sym
        self.group_size = group_size or (-1 if bits == 8 else 128)
        self.ratio = ratio
        self.all_layers = all_layers
        self.sensitivity_metric = sensitivity_metric
        self.quant_method = OVQuantizationMethod(quant_method) if isinstance(quant_method, str) else quant_method
        self.scale_estimation = scale_estimation
        self.gptq = gptq
        self.lora_correction = lora_correction
        self.backup_precision = backup_precision
        self.dtype = dtype
        self.statistics_path = statistics_path
        self.post_init()

    def post_init(self):
        r"""
        Safety checker that arguments are correct
        """
        super().post_init()
        if not (0 <= self.ratio <= 1):
            raise ValueError("`ratio` must between 0 and 1.")
        if self.group_size is not None and self.group_size != -1 and self.group_size <= 0:
            raise ValueError("`group_size` must be greater than 0 or equal to -1")
        if not (self.dataset is None or isinstance(self.dataset, (str, list))):
            raise ValueError(
                f"Dataset must be a instance of either string or list of strings, but found {type(self.dataset)}. "
                f"If you wish to provide a custom dataset, please use the `OVQuantizer` instead."
            )
        if self.dataset is not None and isinstance(self.dataset, str):
            visual_lm_datasets = set(PREDEFINED_VISUAL_LM_DATASETS.keys())
            stable_diffusion_datasets = set(PREDEFINED_SD_DATASETS.keys())
            language_datasets = set(PREDEFINED_LANGUAGE_DATASETS.keys())
            sam_datasets = set(PREDEFINED_SAM_DATASETS.keys())
            if (
                self.dataset
                not in PREDEFINED_CAUSAL_LANGUAGE_DATASETS
                | language_datasets
                | visual_lm_datasets
                | stable_diffusion_datasets
                | sam_datasets
            ):
                raise ValueError(
                    "You have entered a string value for dataset. You can only choose between "
                    f"{language_datasets} for text feature extraction models, "
                    f"{sam_datasets} for SegmentAnything models, "
                    f"{PREDEFINED_CAUSAL_LANGUAGE_DATASETS} for LLMs, {visual_lm_datasets} for visual LLMs or "
                    f"{stable_diffusion_datasets} for diffusion models, but we found {self.dataset}."
                )

        if self.dataset is not None and not (
            self.quant_method in [OVQuantizationMethod.AWQ, OVQuantizationMethod.HYBRID]
            or self.scale_estimation
            or self.gptq
            or self.lora_correction
            or (self.ratio < 1.0 and self.sensitivity_metric != nncf.SensitivityMetric.WEIGHT_QUANTIZATION_ERROR)
        ):
            logger.warning(
                "The provided dataset won't have any effect on the resulting compressed model because no data-aware "
                "quantization algorithm is selected and compression ratio is 1.0."
            )

        if self.dataset is None and self.quant_method == OVQuantizationMethod.AWQ and is_nncf_version("<", "2.17.0"):
            raise ValueError("Data-free AWQ is available starting form NNCF 2.17. Please update nncf package.")

        if self.dtype in ["int4", "int8"]:
            bits = 4 if self.dtype == "int4" else 8
            if self.bits is not None and self.bits != bits:
                logger.warning(
                    f"Overriding `bits` parameter to the value `bits`={bits} to match the given {self.dtype} `dtype`."
                )
            self.bits = bits

        if self.bits not in [4, 8]:
            raise ValueError(f"Only support quantization to [4,8] bits but found {self.bits}")

        if self.bits == 8 and self.dtype:
            if self.ratio != 1:
                raise ValueError(
                    f"For 8-bit quantization, `ratio` is expected to be set to 1.0, but was set to {self.ratio}"
                )
            if self.group_size != -1:
                raise ValueError(
                    f"For 8-bit quantization, `group_size` is expected to be set to -1, but was set to {self.group_size}"
                )
            if self.all_layers:
                raise ValueError("The `all_layers` parameter is not supported for 8-bit quantization")
            if self.sensitivity_metric:
                raise ValueError("The `sensitivity_metric` parameter is not supported for 8-bit quantization")
            if self.quant_method == OVQuantizationMethod.AWQ:
                raise ValueError(
                    "The AWQ algorithm is not supported for 8-bit quantization and got `quant_method='awq'`, please update accordingly"
                )
            if self.scale_estimation:
                raise ValueError(
                    "The Scale Estimation algorithm is not supported for 8-bit quantization and got `scale_estimation=True`, please set `scale_estimation=False`"
                )
            if self.gptq:
                raise ValueError(
                    "The GPTQ algorithm is not supported for 8-bit quantization and got `gptq=True`, please set `gptq=False`"
                )
            if self.lora_correction:
                raise ValueError(
                    "The LoRA Correction algorithm is not supported for 8-bit quantization and got `lora_correction=True`, please set `lora_correction=False`"
                )
            if self.backup_precision is not None:
                raise ValueError(
                    f"The `backup_precision` parameter is not supported for 8-bit quantization and got "
                    f"`backup_precision={self.backup_precision}`, please set `backup_precision=None`"
                )

        if self.backup_precision is not None and self.backup_precision not in ["none", "int8_sym", "int8_asym"]:
            raise ValueError(
                f"`backup_precision` parameter must be on of the following: ['none', 'int8_sym', 'int8_asym'], but found{self.backup_precision}"
            )

        if self.tokenizer is not None and not isinstance(self.tokenizer, str):
            raise ValueError(f"Tokenizer is expected to be a string, but found {self.tokenizer}")

        if self.processor is not None and not isinstance(self.processor, str):
            raise ValueError(f"Processor is expected to be a string, but found {self.processor}")

        if self.dtype is None:
            self.dtype = "int4" if self.bits == 4 else "int8"
        if self.dtype not in ["int4", "int8", "mxfp4", "nf4", "cb4"]:
            raise ValueError(
                "Weights quantization data type must be one of the following: "
                f"['int4', 'int8', 'mxfp4', 'nf4', 'cb4'], but found: {self.dtype}."
            )
        if self.dtype in ["mxfp4", "nf4", "cb4"]:
            if self.dtype == "cb4" and is_nncf_version("<=", "2.17"):
                raise ImportError(
                    "Codebook quantization is currently supported only with NNCF develop. "
                    "Please run `pip install git+https://github.com/openvinotoolkit/nncf.git`."
                )
            if self.bits != 4:
                raise ValueError(
                    f"When applying weight compression with '{self.dtype}' data type, the `bits` parameter must be set to 4, but found {self.bits}"
                )
            if self.dtype == "mxfp4":
                if self.quant_method == OVQuantizationMethod.AWQ:
                    raise ValueError("The AWQ algorithm is not supported for 'mxpf4' data type")
                if self.scale_estimation:
                    raise ValueError("The Scale Estimation algorithm is not supported for 'mxpf4' data type")
                if self.gptq:
                    raise ValueError("The GPTQ algorithm is not supported for 'mxfp4' data type")
                if self.lora_correction:
                    raise ValueError("The LoRA Correction algorithm is not supported for 'mxfp4' data type")
        if self.gptq and self.lora_correction:
            raise ValueError("The GPTQ and LoRA Correction algorithms can't be applied simultaneously")

    def to_nncf_dict(self) -> Dict[str, Any]:
        """
        Returns a dictionary with the variables that are ready to use for nncf.quantize() call.
        """

        signed_bitness = {4: "int4", 8: "int8"}
        mode = self.dtype if self.dtype else signed_bitness[self.bits]
        if mode in signed_bitness.values():
            mode += "_sym" if self.sym else "_asym"
        if mode == "mxfp4":
            mode = "e2m1"
        if mode == "cb4":
            mode = "cb4_f8e4m3"
        mode = nncf.CompressWeightsMode(mode)

        awq = True if self.quant_method == OVQuantizationMethod.AWQ else None
        sensitivity_metric = nncf.SensitivityMetric(self.sensitivity_metric) if self.sensitivity_metric else None
        backup_mode = nncf.BackupMode(self.backup_precision) if self.backup_precision else None
        kwargs = self.kwargs.copy()
        if self.statistics_path:
            advanced_parameters = kwargs.get("advanced_parameters", nncf.AdvancedCompressionParameters())
            advanced_parameters = dataclasses.replace(advanced_parameters, statistics_path=self.statistics_path)
            kwargs["advanced_parameters"] = advanced_parameters
        result = {
            "mode": mode,
            "ratio": self.ratio,
            "group_size": self.group_size,
            "ignored_scope": self.get_ignored_scope_instance(),
            "all_layers": self.all_layers,
            "sensitivity_metric": sensitivity_metric,
            "subset_size": self.num_samples or 128,
            "awq": awq,
            "scale_estimation": self.scale_estimation,
            "gptq": self.gptq,
            "lora_correction": self.lora_correction,
            "backup_mode": backup_mode,
            **kwargs,
        }
        return result


@dataclass
class OVDynamicQuantizationConfig(OVWeightQuantizationConfig):
    def __init__(
        self,
        bits: int = 8,
        sym: bool = False,
        weights_group_size: Optional[int] = None,
        activations_group_size: int = 32,
        **kwargs,
    ):
        super().__init__(bits=bits, sym=sym, group_size=weights_group_size, **kwargs)
        self.activations_group_size = activations_group_size
        logger.warning(
            "OVDynamicQuantizationConfig is deprecated and will be removed in optimum-intel v1.24.0. "
            "Dynamic quantization and KV cache compression are enabled by default starting from OpenVINO 2024.6 and "
            "there is no need to enable them manually. If you need precise control over these parameters, please "
            "provide `DYNAMIC_QUANTIZATION_GROUP_SIZE` and `KV_CACHE_PRECISION` with `ov_config` argument during model "
            "inference."
        )


@dataclass
class OVQuantizationConfig(OVQuantizationConfigBase):
    def __init__(
        self,
        bits: int = 8,
        sym: bool = False,
        ignored_scope: Optional[Union[dict, "nncf.IgnoredScope"]] = None,
        num_samples: Optional[int] = None,
        model_type: str = "transformer",
        fast_bias_correction: bool = True,
        overflow_fix: str = "disable",
        dataset: Optional[str] = None,
        tokenizer: Optional[str] = None,
        processor: Optional[str] = None,
        trust_remote_code: bool = False,
        smooth_quant_alpha: Optional[float] = None,
        dtype: Optional[str] = "int8",
        **kwargs,
    ):
        """
        Configuration class containing parameters related to model quantization with NNCF. Compared to weight
        compression, during quantization both weights and activations are converted to lower precision.
        For weight-only model quantization please see OVWeightQuantizationConfig.
        Args:
            bits (`int`, defaults to 8):
                The number of bits to quantize to.
            sym (`bool`, defaults to `False`):
                Whether to use symmetric quantization on the activations. Symmetric quantization will be applied on the weights in any case.
            ignored_scope (`dict` or `nncf.IgnoredScope`, *optional*):
                An ignored scope that defines the list of model nodes to be ignored during quantization. Dictionary
                entries provided via this argument are used to create an instance of `nncf.IgnoredScope` class.
            num_samples (`int`, *optional*):
                The maximum number of samples composing the calibration dataset.
            model_type (`str`, defaults to "transformer"):
                Model type is needed to specify additional patterns in the model. Supported only `transformer` now.
            fast_bias_correction (`bool`, defaults to True):
                Whether to apply fast or full bias correction algorithm.
            overflow_fix (`str`, default to "disable"):
                Parameter for controlling overflow fix setting.
            dataset (`str`, *optional*):
                The dataset used for quantization. For language models the allowed values are
                ['auto', 'wikitext2','c4','c4-new']. For text-to-speech model quantization the allowed value is 'librispeech'.
            tokenizer (`str`, *optional*):
                The tokenizer used to process the dataset. You can pass either:
                    - A string, the *model id* of a predefined tokenizer hosted inside a model repo on huggingface.co.
                        Valid model ids can be located at the root-level, like `bert-base-uncased`, or namespaced under a
                        user or organization name, like `dbmdz/bert-base-german-cased`.
                    - A path to a *directory* containing vocabulary files required by the tokenizer, for instance saved
                        using the [`~PreTrainedTokenizer.save_pretrained`] method, e.g., `./my_model_directory/`.
            processor (`str`, *optional*):
                A transformers processor used to process inputs for multi-modal models. You can pass either:
                    - A string, the *model id* of a predefined processor hosted inside a model repo on huggingface.co.
                    - A path to a *directory* containing files required by the processor, for instance saved
                        using the [`~AutoProcessor.save_pretrained`] method, e.g., `./my_model_directory/`.
            trust_remote_code (`bool`, defaults to `False`):
                Allows to use custom code for the modeling hosted in the model repository. This option should only be set
                for repositories you trust and in which you have read the code, as it will execute on your local machine
                arbitrary code present in the model repository.
            smooth_quant_alpha (`float`, *optional*):
                SmoothQuant alpha parameter that improves the distribution of activations before MatMul layers and
                reduces quantization error.
            dtype (`str`, defaults to "int8"):
                Data type activations are compressed to. Possible values: ['int8', 'f8e4m3', 'f8e5m2'].
            kwargs: Additional parameters for nncf.quantize() call.
        """
        activation_format = kwargs.pop("activation_format", None)
        if activation_format is not None:
            logger.warning(
                "The `activation_format` parameter is deprecated and will be removed in optimum-intel v1.24.0. "
                "Please use `dtype` instead."
            )
            dtype = activation_format
        super().__init__(
            ignored_scope=ignored_scope,
            num_samples=num_samples,
            dataset=dataset,
            tokenizer=tokenizer,
            processor=processor,
            trust_remote_code=trust_remote_code,
            **kwargs,
        )
        self.bits = bits
        self.sym = sym
        self.model_type = model_type
        self.fast_bias_correction = fast_bias_correction
        self.overflow_fix = overflow_fix
        self.smooth_quant_alpha = smooth_quant_alpha
        self.dtype = dtype

        f8_dtypes = ["f8e4m3", "f8e5m2"]
        if self.dtype in f8_dtypes:
            self.sym = True
        self.post_init()

    def post_init(self):
        r"""
        Safety checker that arguments are correct
        """
        super().post_init()

        if self.dataset is not None and isinstance(self.dataset, str):
            speech_to_text_datasets = set(PREDEFINED_SPEECH_TO_TEXT_DATASETS.keys())
            visual_lm_datasets = set(PREDEFINED_VISUAL_LM_DATASETS.keys())
            stable_diffusion_datasets = set(PREDEFINED_SD_DATASETS.keys())
            language_datasets = set(PREDEFINED_LANGUAGE_DATASETS.keys())
            sam_datasets = set(PREDEFINED_SAM_DATASETS.keys())
            if (
                self.dataset
                not in PREDEFINED_CAUSAL_LANGUAGE_DATASETS
                | language_datasets
                | speech_to_text_datasets
                | stable_diffusion_datasets
                | visual_lm_datasets
                | sam_datasets
            ):
                raise ValueError(
                    "You can only choose between the following datasets:"
                    f"{language_datasets} for text feature extraction models, "
                    f"{PREDEFINED_CAUSAL_LANGUAGE_DATASETS} for LLMs, "
                    f"{speech_to_text_datasets} for speech-to-text models, "
                    f"{sam_datasets} for SegmentAnything models, "
                    f"{visual_lm_datasets} for visual LLMs or "
                    f"{stable_diffusion_datasets} for diffusion models, but we found {self.dataset}."
                )

        if self.bits != 8:
            raise ValueError(f"Only support 8-bit for static quantization but found {self.bits}")

        if self.smooth_quant_alpha is not None and (
            self.smooth_quant_alpha != -1 and not (0 <= self.smooth_quant_alpha <= 1)
        ):
            raise ValueError(
                f"SmoothQuant alpha parameter can equal -1 or be in range [0, 1], but found {self.smooth_quant_alpha}"
            )

    def to_nncf_dict(self) -> Dict[str, Any]:
        """
        Returns a dictionary with the variables that are ready to use for nncf.compress_weights() call.
        """

        # Merge advanced parameters from kwargs if they were provided
        kwargs_copy = copy.deepcopy(self.kwargs)
        advanced_parameters = kwargs_copy.pop("advanced_parameters", nncf.AdvancedQuantizationParameters())
        advanced_parameters.overflow_fix = nncf.OverflowFix(self.overflow_fix)
        if self.smooth_quant_alpha:
            advanced_parameters.smooth_quant_alphas.matmul = self.smooth_quant_alpha

        mode_map = {"f8e4m3": "fp8_e4m3", "f8e5m2": "fp8_e5m2"}
        mode = mode_map.get(self.dtype)

        preset = "performance" if self.sym else "mixed"
        preset = nncf.QuantizationPreset(preset)
        model_type = nncf.ModelType(self.model_type)

        return {
            "mode": mode,
            "preset": preset,
            "subset_size": self.num_samples or 128,
            "fast_bias_correction": self.fast_bias_correction,
            "model_type": model_type,
            "ignored_scope": self.get_ignored_scope_instance(),
            "advanced_parameters": advanced_parameters,
            **kwargs_copy,
        }


class OVConfig(BaseConfig):
    CONFIG_NAME = "openvino_config.json"
    FULL_CONFIGURATION_FILE = "openvino_config.json"

    def __init__(
        self,
        input_info: Optional[List] = None,
        save_onnx_model: bool = False,
        quantization_config: Optional[Union[dict, OVQuantizationConfigBase]] = None,
        dtype: Optional[str] = None,
        **kwargs,
    ):
        super().__init__()
        self.input_info = input_info
        self.save_onnx_model = save_onnx_model
        self.optimum_version = kwargs.pop("optimum_version", None)
        if isinstance(quantization_config, dict):
            quantization_config = _quantization_config_from_dict(quantization_config)
        self.quantization_config = quantization_config
        if self.quantization_config is not None:
            self.dtype = self._get_dtype(self.quantization_config)
        else:
            self.dtype = dtype

    def add_input_info(self, model_inputs: Dict, force_batch_one: bool = False):
        self.input_info = [
            {
                "sample_size": [1] + list(value.shape[1:]) if force_batch_one else list(value.shape),
                "type": "long" if value.dtype is torch.int64 else "float",
                "keyword": name,
            }
            for name, value in model_inputs.items()
        ]

    def _to_dict_safe(self, to_diff_dict: bool = False) -> Dict[str, Any]:
        class ConfigStub:
            def to_dict(self):
                return None

            def to_diff_dict(self):
                return None

        if self.quantization_config is None:
            # Parent to_dict() implementation does not support quantization_config being None
            self_copy = copy.deepcopy(self)
            self_copy.quantization_config = ConfigStub()
            result = self_copy.to_diff_dict() if to_diff_dict else self_copy.to_dict()
        else:
            result = super().to_diff_dict() if to_diff_dict else super().to_dict()
        return result

    @staticmethod
    def _get_dtype(quantization_config):
        if isinstance(quantization_config, (OVWeightQuantizationConfig, OVQuantizationConfig)):
            dtype = quantization_config.dtype
        elif isinstance(quantization_config, OVMixedQuantizationConfig):
            wc_dtype = quantization_config.weight_quantization_config.dtype
            q_dtype = quantization_config.full_quantization_config.dtype
            dtype = f"{wc_dtype}_{q_dtype}"
        elif isinstance(quantization_config, OVPipelineQuantizationConfig):
            dtypes = [OVConfig._get_dtype(config) for config in quantization_config.quantization_configs.values()]
            dtype = "_".join(dtypes)
        else:
            raise ValueError(f"Unsupported type of quantization config: {type(quantization_config)}")
        return dtype

    def to_dict(self) -> Dict[str, Any]:
        return self._to_dict_safe(to_diff_dict=False)

    def to_diff_dict(self) -> Dict[str, Any]:
        return self._to_dict_safe(to_diff_dict=True)


class OVMixedQuantizationConfig(OVQuantizationConfigBase):
    def __init__(
        self,
        weight_quantization_config: Union[OVWeightQuantizationConfig, dict],
        full_quantization_config: Union[OVQuantizationConfig, dict],
        ignored_scope: Optional[Union[dict, "nncf.IgnoredScope"]] = None,
        num_samples: Optional[int] = None,
        dataset: Optional[Union[str, List[str]]] = None,
        tokenizer: Optional[str] = None,
        processor: Optional[str] = None,
        trust_remote_code: bool = False,
        **kwargs,
    ):
        """
        Configuration class for mixed quantization where we separately quantize:
            (1) weights of weighted layers to the precision given in the `weight_quantization_config`, and
            (2) weights and activations of other possible layers; precision is given in the `full_quantization_config`.

        By default, weights of all weighted layers are quantized in the first step. In the second step activations of
        weighted and non-weighted layers are quantized. If some layers are instructed to be ignored in the first step
        with `weight_quantization_config.ignored_scope` parameter, both weights and activations of these layers are
        quantized to the precision given in the `full_quantization_config`.

        Args:
            weight_quantization_config (`OVWeightQuantizationConfig` or `dict`):
                Configuration related to weight quantization.
            full_quantization_config (`OVQuantizationConfig` or `dict`):
                Configuration related to full quantization.
            ignored_scope (`dict` or `nncf.IgnoredScope`, *optional*):
                An ignored scope that defines the list of model nodes to be ignored during quantization. Dictionary
                entries provided via this argument are used to create an instance of `nncf.IgnoredScope` class.
                Ignored scope provided here will be used for both weight and full quantization steps.
            num_samples (`int`, *optional*):
                The maximum number of samples composing the calibration dataset.
            dataset (`str or List[str]`, *optional*):
                The dataset used for data-aware optimization with NNCF.
            tokenizer (`str`, *optional*):
                The tokenizer used to process the dataset.
            processor (`str`, *optional*):
                A transformers processor used to process the dataset inputs.
            trust_remote_code (`bool`, defaults to `False`):
                Allows to use custom code for the modeling hosted in the model repository. This option should only be
                set for repositories you trust and in which you have read the code, as it will execute on your local
                machine arbitrary code present in the model repository.
            **kwargs:
        """
        self.weight_quantization_config = self._initialize_quantization_config(
            weight_quantization_config, OVWeightQuantizationConfig
        )
        wqc = self.weight_quantization_config

        self.full_quantization_config = self._initialize_quantization_config(
            full_quantization_config, OVQuantizationConfig
        )
        fqc = self.full_quantization_config

        if fqc.dtype in ["f8e4m3", "f8e5m2"] and wqc.backup_precision is None:
            # Here we simulate FP8 backup weight compression precision through full quantization: during weight
            # compression step some weighted layers are kept in original precision and later are compressed to FP8
            # during full precision quantization step.
            # The issue with current approach is that if one provides an ignored scope for the full quantization step,
            # then the weights of the layers under this ignored scope won't be compressed to FP8.
            # TODO: remove once there is support for FP8 weight compression in NNCF
            wqc.backup_precision = "none"

        # Pull dataset-related parameters from child configs
        num_samples = max((num_samples or 0, wqc.num_samples or 0, fqc.num_samples or 0)) or None
        dataset = dataset or wqc.dataset or fqc.dataset
        tokenizer = tokenizer or wqc.tokenizer or fqc.tokenizer
        processor = processor or wqc.processor or fqc.processor
        trust_remote_code = trust_remote_code or wqc.trust_remote_code or fqc.trust_remote_code
        super().__init__(
            ignored_scope=ignored_scope,
            num_samples=num_samples,
            dataset=dataset,
            tokenizer=tokenizer,
            processor=processor,
            trust_remote_code=trust_remote_code,
            **kwargs,
        )

        self.post_init()

    def post_init(self):
        super().post_init()

        if self.weight_quantization_config.dtype == "nf4" and self.full_quantization_config.dtype in [
            "f8e4m3",
            "f8e5m2",
        ]:
            logger.warning(
                "\n`nf4_f8e4m3` and `nf4_f8e5m2` mixed precision quantization modes are deprecated and will be "
                "removed in optimum-intel v1.26. Please use `cb4_f8e4m3` instead.\n"
            )

    @staticmethod
    def _initialize_quantization_config(
        config: Union[dict, OVWeightQuantizationConfig, OVQuantizationConfig],
        config_type: Type[Union[OVWeightQuantizationConfig, OVQuantizationConfig]],
    ):
        if isinstance(config, dict):
            return config_type.from_dict(config)
        elif isinstance(config, config_type):
            return config.clone()
        else:
            raise ValueError(
                f"Unsupported type of quantization config. Expected either a dictionary or an instance of "
                f"{config_type}, but found: {type(config)}."
            )

    def to_dict(self):
        result = super().to_dict()
        result["weight_quantization_config"] = self.weight_quantization_config.to_dict()
        result["full_quantization_config"] = self.full_quantization_config.to_dict()
        return result


class OVPipelineQuantizationConfig(OVQuantizationConfigBase):
    def __init__(
        self,
        quantization_configs: Dict[str, Union[Dict, OVQuantizationConfigBase]],
        num_samples: Optional[int] = None,
        dataset: Optional[Union[str, List[str]]] = None,
        tokenizer: Optional[str] = None,
        processor: Optional[str] = None,
        trust_remote_code: Optional[bool] = False,
        **kwargs,
    ):
        """
        Configuration class for quantization of multimodel pipelines.
        For each submodel in the pipeline, a separate quantization config can be provided. If the config is not provided for a
        submodel, it won't be quantized.

        Args:
            quantization_configs (Dict[str, Union[Dict, OVQuantizationConfigBase]]):
                A dictionary where keys are submodel names and values are either dictionaries or instances of
                `OVQuantizationConfigBase` containing quantization configurations for each submodel in the pipeline.
            num_samples (Optional[int]):
                The maximum number of samples composing the calibration dataset. Defaults to None.
            dataset (Optional[Union[str, List[str]]]):
                The dataset used for data-aware optimization with NNCF. Can be a string or a list of strings. Defaults to None.
            tokenizer (Optional[str]):
                The tokenizer used to process the dataset. Can be a model ID or a path to a directory containing
                tokenizer files. Defaults to None.
            processor (Optional[str]):
                A transformers processor used to process the dataset inputs. Can be a model ID or a path to a
                directory containing processor files. Defaults to None.
            trust_remote_code (Optional[bool]):
                If True, allows the use of custom code hosted in the model repository. This should only be set for repositories
                you trust, as it will execute arbitrary code on your local machine. Defaults to False.
            **kwargs:
                Additional parameters for the configuration.
        """

        def or_op(a, b):
            return a or b

        if kwargs.pop("ignored_scope", None) is not None:
            logger.warning(
                "`ignored_scope` parameter is not supported for pipeline quantization. It will be ignored. "
                "Please use `ignored_scope` parameter in the submodel configs instead."
            )

        quantization_configs = copy.deepcopy(quantization_configs)
        for submodel_name, submodel_config in quantization_configs.items():
            if isinstance(submodel_config, dict):
                quantization_configs[submodel_name] = _quantization_config_from_dict(submodel_config)

        # Pull dataset-related parameters from child configs
        configs = quantization_configs.values()
        num_samples = max((num_samples or 0, *(config.num_samples or 0 for config in configs))) or None
        dataset = reduce(or_op, (dataset, *(config.dataset for config in configs)))
        tokenizer = reduce(or_op, (tokenizer, *(config.tokenizer for config in configs)))
        processor = reduce(or_op, (processor, *(config.processor for config in configs)))
        trust_remote_code = reduce(or_op, (trust_remote_code, *(config.trust_remote_code for config in configs)))

        super().__init__(
            ignored_scope=None,
            num_samples=num_samples,
            dataset=dataset,
            tokenizer=tokenizer,
            processor=processor,
            trust_remote_code=trust_remote_code,
            **kwargs,
        )
        self.quantization_configs = quantization_configs
        self.post_init()

    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["quantization_configs"] = {name: config.to_dict() for name, config in self.quantization_configs.items()}
        return result

    def post_init(self):
        super().post_init()
        for submodel_config in self.quantization_configs.values():
            submodel_config.post_init()


def _quantization_config_from_dict(config_dict: Dict[str, Any]) -> OVQuantizationConfigBase:
    """
    Helper function to create a quantization config from a dictionary.
    """

    # Check for OVMixedQuantizationConfig
    if "weight_quantization_config" in config_dict and "full_quantization_config" in config_dict:
        return OVMixedQuantizationConfig.from_dict(config_dict)

    # Check for OVPipelineQuantizationConfig
    if "quantization_configs" in config_dict:
        return OVPipelineQuantizationConfig.from_dict(config_dict)

    # Either OVWeightQuantizationConfig or OVQuantizationConfig
    # Try to detect the type of config based on the keys present in the dictionary
    wq_args = set(inspect.getfullargspec(OVWeightQuantizationConfig.__init__).args)
    fq_args = set(inspect.getfullargspec(OVQuantizationConfig.__init__).args)
    common_args = wq_args & fq_args
    wq_only_args = wq_args - common_args
    fq_only_args = fq_args - common_args
    if any(arg in wq_only_args for arg in config_dict):
        return OVWeightQuantizationConfig.from_dict(config_dict)
    if any(arg in fq_only_args for arg in config_dict):
        return OVQuantizationConfig.from_dict(config_dict)

    # Try to create instances of both configs and check which one is valid
    try:
        wq_config = OVWeightQuantizationConfig.from_dict(config_dict)
    except ValueError as wc_exception:  # noqa: F841
        wq_config = None
    try:
        fq_config = OVQuantizationConfig.from_dict(config_dict)
    except ValueError as fq_exception:  # noqa: F841
        fq_config = None
    if (wq_config is None) != (fq_config is None):
        return wq_config or fq_config
    if wq_config is None and fq_config is None:
        raise ValueError(
            "The provided configuration dictionary does not match the expected structure for either "
            "OVWeightQuantizationConfig or OVQuantizationConfig. "
            f"Exceptions encountered: {wc_exception} \n {fq_exception}"  # noqa: F821
        )

    # Try to determine the type of config based on the `weight_only` parameter
    weight_only = config_dict.get("weight_only", None)
    if weight_only:
        return wq_config
    if weight_only is False:
        return fq_config

    # If everything else fails, default to OVWeightQuantizationConfig
    logger.warning(
        "Can't determine type of OV quantization config. Please specify explicitly whether you intend to "
        "run weight-only quantization or not with `weight_only` parameter. Creating an instance of "
        "OVWeightQuantizationConfig."
    )
    return wq_config
