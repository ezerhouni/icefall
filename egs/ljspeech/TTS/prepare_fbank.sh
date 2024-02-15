#!/usr/bin/env bash

# fix segmentation fault reported in https://github.com/k2-fsa/icefall/issues/674
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python

set -eou pipefail

stage=0
stop_stage=100

dl_dir=$PWD/download

. shared/parse_options.sh || exit 1

# All files generated by this script are saved in "data".
# You can safely remove "data" and rerun this script to regenerate it.
mkdir -p data

log() {
  # This function is from espnet
  local fname=${BASH_SOURCE[1]##*/}
  echo -e "$(date '+%Y-%m-%d %H:%M:%S') (${fname}:${BASH_LINENO[0]}:${FUNCNAME[1]}) $*"
}

log "dl_dir: $dl_dir"

if [ $stage -le -1 ] && [ $stop_stage -ge -1 ]; then
  log "Stage -1: build monotonic_align lib"
  if [ ! -d vits/monotonic_align/build ]; then
    cd vits/monotonic_align
    python setup.py build_ext --inplace
    cd ../../
  else
    log "monotonic_align lib already built"
  fi
fi

if [ $stage -le 0 ] && [ $stop_stage -ge 0 ]; then
  log "Stage 0: Download data"

  # The directory $dl_dir/LJSpeech-1.1 will contain:
  #   - wavs, which contains the audio files
  #   - metadata.csv, which provides the transcript text for each audio clip

  # If you have pre-downloaded it to /path/to/LJSpeech-1.1, you can create a symlink
  #
  #   ln -sfv /path/to/LJSpeech-1.1 $dl_dir/LJSpeech-1.1
  #
  if [ ! -d $dl_dir/LJSpeech-1.1 ]; then
    lhotse download ljspeech $dl_dir
  fi
fi

if [ $stage -le 1 ] && [ $stop_stage -ge 1 ]; then
  log "Stage 1: Prepare LJSpeech manifest"
  # We assume that you have downloaded the LJSpeech corpus
  # to $dl_dir/LJSpeech
  mkdir -p data/manifests
  if [ ! -e data/manifests/.ljspeech.done ]; then
    lhotse prepare ljspeech $dl_dir/LJSpeech-1.1 data/manifests
    touch data/manifests/.ljspeech.done
  fi
fi

if [ $stage -le 2 ] && [ $stop_stage -ge 2 ]; then
  log "Stage 2: Compute fbank for LJSpeech"
  mkdir -p data/fbank
  if [ ! -e data/fbank/.ljspeech.done ]; then
    ./local_fbank/compute_fbank_ljspeech.py
    touch data/fbank/.ljspeech.done
  fi

  if [ ! -e data/fbank/.ljspeech-validated.done ]; then
    log "Validating data/fbank for LJSpeech"
    python3 ./local_fbank/validate_manifest.py \
      data/fbank/ljspeech_cuts_all.jsonl.gz
    touch data/fbank/.ljspeech-validated.done
  fi
fi

if [ $stage -le 3 ] && [ $stop_stage -ge 3 ]; then
  log "Stage 3: Prepare phoneme tokens for LJSpeech"
  if [ ! -e data/fbank/.ljspeech_with_token.done ]; then
    ./local_fbank/prepare_tokens_ljspeech.py
    mv data/fbank/ljspeech_cuts_with_tokens_all.jsonl.gz \
      data/fbank/ljspeech_cuts_all.jsonl.gz
    touch data/fbank/.ljspeech_with_token.done
  fi
fi

if [ $stage -le 4 ] && [ $stop_stage -ge 4 ]; then
  log "Stage 4: Split the LJSpeech cuts into train, valid and test sets"
  if [ ! -e data/fbank/.ljspeech_split.done ]; then
    lhotse subset --last 600 \
      data/fbank/ljspeech_cuts_all.jsonl.gz \
      data/fbank/ljspeech_cuts_validtest.jsonl.gz
    lhotse subset --first 100 \
      data/fbank/ljspeech_cuts_validtest.jsonl.gz \
      data/fbank/ljspeech_cuts_valid.jsonl.gz
    lhotse subset --last 500 \
      data/fbank/ljspeech_cuts_validtest.jsonl.gz \
      data/fbank/ljspeech_cuts_test.jsonl.gz

    rm data/fbank/ljspeech_cuts_validtest.jsonl.gz

    n=$(( $(gunzip -c data/fbank/ljspeech_cuts_all.jsonl.gz | wc -l) - 600 ))
    lhotse subset --first $n  \
      data/fbank/ljspeech_cuts_all.jsonl.gz \
      data/fbank/ljspeech_cuts_train.jsonl.gz
      touch data/fbank/.ljspeech_split.done
  fi
fi

if [ $stage -le 5 ] && [ $stop_stage -ge 5 ]; then
  log "Stage 5: Generate token file"
  # We assume you have installed g2p_en and espnet_tts_frontend.
  # If not, please install them with:
  #   - g2p_en: `pip install g2p_en`, refer to https://github.com/Kyubyong/g2p
  #   - espnet_tts_frontend, `pip install espnet_tts_frontend`, refer to https://github.com/espnet/espnet_tts_frontend/
  if [ ! -e data/tokens.txt ]; then
    ./local_fbank/prepare_token_file.py \
      --manifest-file data/fbank/ljspeech_cuts_train.jsonl.gz \
      --tokens data/tokens.txt
  fi
fi
