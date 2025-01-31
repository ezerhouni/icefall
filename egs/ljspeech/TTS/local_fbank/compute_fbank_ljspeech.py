#!/usr/bin/env python3
# Copyright    2021-2023  Xiaomi Corp.        (authors: Fangjun Kuang,
#                                                       Zengwei Yao)
#
# See ../../../../LICENSE for clarification regarding multiple authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
This file computes fbank features of the LJSpeech dataset.
It looks for manifests in the directory data/manifests.

The generated spectrogram features are saved in data/spectrogram.
"""

import logging
import os
from pathlib import Path

import torch
from lhotse import CutSet, Fbank, FbankConfig, LilcomChunkyWriter, load_manifest
from lhotse.audio import RecordingSet
from lhotse.supervision import SupervisionSet

from icefall.utils import get_executor

# Torch's multithreaded behavior needs to be disabled or
# it wastes a lot of CPU and slow things down.
# Do this outside of main() in case it needs to take effect
# even when we are not invoking the main (e.g. when spawning subprocesses).
torch.set_num_threads(1)
torch.set_num_interop_threads(1)


def compute_spectrogram_ljspeech():
    src_dir = Path("data/manifests")
    output_dir = Path("data/fbank")
    num_jobs = min(4, os.cpu_count())
    num_mel_bins = 80

    prefix = "ljspeech"
    suffix = "jsonl.gz"
    partition = "all"
    sampling_rate = 16000
    frame_length = 1024 / sampling_rate  # (in second)
    frame_shift = 256 / sampling_rate  # (in seconds)

    recordings = load_manifest(
        src_dir / f"{prefix}_recordings_{partition}.{suffix}", RecordingSet
    )
    supervisions = load_manifest(
        src_dir / f"{prefix}_supervisions_{partition}.{suffix}", SupervisionSet
    )

    extractor = Fbank(
        FbankConfig(
            num_mel_bins=num_mel_bins,
            sampling_rate=sampling_rate,
            frame_length=frame_length,
            frame_shift=frame_shift,
        )
    )

    with get_executor() as ex:  # Initialize the executor only once.
        cuts_filename = f"{prefix}_cuts_{partition}.{suffix}"
        if (output_dir / cuts_filename).is_file():
            logging.info(f"{cuts_filename} already exists - skipping.")
            return
        logging.info(f"Processing {partition}")
        cut_set = CutSet.from_manifests(
            recordings=recordings, supervisions=supervisions
        ).resample(sampling_rate)

        cut_set = cut_set.compute_and_store_features(
            extractor=extractor,
            storage_path=f"{output_dir}/{prefix}_feats_{partition}",
            # when an executor is specified, make more partitions
            num_jobs=num_jobs if ex is None else 80,
            executor=ex,
            storage_type=LilcomChunkyWriter,
        )
        cut_set.to_file(output_dir / cuts_filename)


if __name__ == "__main__":
    formatter = "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s"

    logging.basicConfig(format=formatter, level=logging.INFO)
    compute_spectrogram_ljspeech()
