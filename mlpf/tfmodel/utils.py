import os
import yaml
from pathlib import Path
import datetime
import platform
import random
import glob
import numpy as np
from tqdm import tqdm

import tensorflow as tf

from tfmodel.data import Dataset
from tfmodel.onecycle_scheduler import OneCycleScheduler, MomentumOneCycleScheduler


def load_config(config_file_path):
    with open(config_file_path, "r") as ymlfile:
        cfg = yaml.load(ymlfile, Loader=yaml.FullLoader)
    return cfg


def parse_config(config, ntrain=None, ntest=None, weights=None):
    config_file_stem = Path(config).stem
    config = load_config(config)
    tf.config.run_functions_eagerly(config["tensorflow"]["eager"])
    global_batch_size = config["setup"]["batch_size"]
    n_epochs = config["setup"]["num_epochs"]
    if ntrain:
        n_train = ntrain
    else:
        n_train = config["setup"]["num_events_train"]
    if ntest:
        n_test = ntest
    else:
        n_test = config["setup"]["num_events_test"]

    if "multi_output" not in config["setup"]:
        config["setup"]["multi_output"] = True

    if weights is None:
        weights = config["setup"]["weights"]

    return config, config_file_stem, global_batch_size, n_train, n_test, n_epochs, weights


def create_experiment_dir(prefix=None, suffix=None):
    if prefix is None:
        train_dir = Path("experiments") / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    else:
        train_dir = Path("experiments") / (prefix + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))

    if suffix is not None:
        train_dir = train_dir.with_name(train_dir.name + "." + platform.node())

    train_dir.mkdir(parents=True)
    return str(train_dir)


def get_strategy(global_batch_size):
    try:
        gpus = [int(x) for x in os.environ.get("CUDA_VISIBLE_DEVICES", "0").split(",")]
        num_gpus = len(gpus)
        print("num_gpus=", num_gpus)
        if num_gpus > 1:
            strategy = tf.distribute.MirroredStrategy()
            global_batch_size = num_gpus * global_batch_size
        else:
            strategy = tf.distribute.OneDeviceStrategy("gpu:0")
    except Exception as e:
        print("fallback to CPU", e)
        strategy = tf.distribute.OneDeviceStrategy("cpu")
        num_gpus = 0
    return strategy, global_batch_size


def get_lr_schedule(config, lr, steps):
    callbacks = []
    schedule = config["setup"]["lr_schedule"]
    if schedule == "onecycle":
        onecycle_cfg = config["onecycle"]
        lr_schedule = OneCycleScheduler(
            lr_max=lr,
            steps=steps,
            mom_min=onecycle_cfg["mom_min"],
            mom_max=onecycle_cfg["mom_max"],
            warmup_ratio=onecycle_cfg["warmup_ratio"],
            div_factor=onecycle_cfg["div_factor"],
            final_div=onecycle_cfg["final_div"],
        )
        callbacks.append(
            MomentumOneCycleScheduler(
                steps=steps,
                mom_min=onecycle_cfg["mom_min"],
                mom_max=onecycle_cfg["mom_max"],
                warmup_ratio=onecycle_cfg["warmup_ratio"],
            )
        )
    elif schedule == "exponentialdecay":
        lr_schedule = tf.keras.optimizers.schedules.ExponentialDecay(
            lr,
            decay_steps=steps,
            decay_rate=0.99,
            staircase=True,
        )
    return lr_schedule, callbacks


def compute_weights_invsqrt(X, y, w):
    wn = tf.cast(tf.shape(w)[-1], tf.float32) / tf.sqrt(w)
    wn *= tf.cast(X[:, 0] != 0, tf.float32)
    # wn /= tf.reduce_sum(wn)
    return X, y, wn


def compute_weights_none(X, y, w):
    wn = tf.ones_like(w)
    wn *= tf.cast(X[:, 0] != 0, tf.float32)
    return X, y, wn


def get_weights_func(config):
    sampling = config["setup"]["sample_weights"]
    if sampling == "inverse_sqrt":
        return compute_weights_invsqrt
    elif sampling == "none":
        return compute_weights_none
    else:
        raise ValueError("Only supported weight samplings are 'inverse_sqrt' and 'none'.")


def targets_multi_output(num_output_classes):
    def func(X, y, w):
        return (
            X,
            {
                "cls": tf.one_hot(tf.cast(y[:, :, 0], tf.int32), num_output_classes),
                "charge": y[:, :, 1:2],
                "pt": y[:, :, 2:3],
                "eta": y[:, :, 3:4],
                "sin_phi": y[:, :, 4:5],
                "cos_phi": y[:, :, 5:6],
                "energy": y[:, :, 6:7],
            },
            w,
        )

    return func


def get_dataset_def(config):
    cds = config["dataset"]

    return Dataset(
        num_input_features=int(cds["num_input_features"]),
        num_output_features=int(cds["num_output_features"]),
        padded_num_elem_size=int(cds["padded_num_elem_size"]),
        raw_path=cds.get("raw_path", None),
        raw_files=cds.get("raw_files", None),
        processed_path=cds["processed_path"],
        validation_file_path=cds["validation_file_path"],
        schema=cds["schema"],
    )


def get_train_val_datasets(config, global_batch_size, n_train, n_test):
    dataset_def = get_dataset_def(config)

    tfr_files = sorted(glob.glob(dataset_def.processed_path))
    if len(tfr_files) == 0:
        raise Exception("Could not find any files in {}".format(dataset_def.processed_path))

    random.shuffle(tfr_files)
    dataset = tf.data.TFRecordDataset(tfr_files).map(
        dataset_def.parse_tfr_element, num_parallel_calls=tf.data.experimental.AUTOTUNE
    )

    # Due to TFRecords format, the length of the dataset is not known beforehand
    num_events = 0
    for _ in dataset:
        num_events += 1
    print("dataset loaded, len={}".format(num_events))

    weight_func = get_weights_func(config)
    assert n_train + n_test <= num_events

    # Padded shapes
    ps = (
        tf.TensorShape([dataset_def.padded_num_elem_size, dataset_def.num_input_features]),
        tf.TensorShape([dataset_def.padded_num_elem_size, dataset_def.num_output_features]),
        tf.TensorShape(
            [
                dataset_def.padded_num_elem_size,
            ]
        ),
    )

    ds_train = dataset.take(n_train).map(weight_func).padded_batch(global_batch_size, padded_shapes=ps)
    ds_test = dataset.skip(n_train).take(n_test).map(weight_func).padded_batch(global_batch_size, padded_shapes=ps)

    if config["setup"]["multi_output"]:
        dataset_transform = targets_multi_output(config["dataset"]["num_output_classes"])
        ds_train = ds_train.map(dataset_transform)
        ds_test = ds_test.map(dataset_transform)
    else:
        dataset_transform = None

    ds_train_r = ds_train.repeat(config["setup"]["num_epochs"])
    ds_test_r = ds_test.repeat(config["setup"]["num_epochs"])

    return ds_train_r, ds_test_r, dataset_transform


def prepare_val_data(config, dataset_def, single_file=False):
    if single_file:
        val_filelist = dataset_def.val_filelist[:1]
    else:
        val_filelist = dataset_def.val_filelist
    if config["setup"]["num_val_files"] > 0:
        val_filelist = val_filelist[: config["setup"]["num_val_files"]]

    Xs = []
    ygens = []
    ycands = []
    for fi in tqdm(val_filelist[:1], desc="Preparing validation data"):
        X, ygen, ycand = dataset_def.prepare_data(fi)
        Xs.append(np.concatenate(X))
        ygens.append(np.concatenate(ygen))
        ycands.append(np.concatenate(ycand))

    assert len(Xs) > 0, "Xs is empty"
    X_val = np.concatenate(Xs)
    ygen_val = np.concatenate(ygens)
    ycand_val = np.concatenate(ycands)

    return X_val, ygen_val, ycand_val


def set_config_loss(config, trainable):
    if trainable == "classification":
        config["dataset"]["pt_loss_coef"] = 0.0
        config["dataset"]["eta_loss_coef"] = 0.0
        config["dataset"]["sin_phi_loss_coef"] = 0.0
        config["dataset"]["cos_phi_loss_coef"] = 0.0
        config["dataset"]["energy_loss_coef"] = 0.0
    elif trainable == "regression":
        config["dataset"]["classification_loss_coef"] = 0.0
        config["dataset"]["charge_loss_coef"] = 0.0
    elif trainable == "all":
        pass
    return config


def get_class_loss(config):
    if config["setup"]["classification_loss_type"] == "categorical_cross_entropy":
        cls_loss = tf.keras.losses.CategoricalCrossentropy(from_logits=False)
    elif config["setup"]["classification_loss_type"] == "sigmoid_focal_crossentropy":
        cls_loss = tfa.losses.sigmoid_focal_crossentropy
    else:
        raise KeyError("Unknown classification loss type: {}".format(config["setup"]["classification_loss_type"]))
    return cls_loss


def get_loss_dict(config):
    cls_loss = get_class_loss(config)
    loss_dict = {
        "cls": cls_loss,
        "charge": getattr(tf.keras.losses, config["dataset"].get("charge_loss", "MeanSquaredError"))(),
        "pt": getattr(tf.keras.losses, config["dataset"].get("pt_loss", "MeanSquaredError"))(),
        "eta": getattr(tf.keras.losses, config["dataset"].get("eta_loss", "MeanSquaredError"))(),
        "sin_phi": getattr(tf.keras.losses, config["dataset"].get("sin_phi_loss", "MeanSquaredError"))(),
        "cos_phi": getattr(tf.keras.losses, config["dataset"].get("cos_phi_loss", "MeanSquaredError"))(),
        "energy": getattr(tf.keras.losses, config["dataset"].get("energy_loss", "MeanSquaredError"))(),
    }
    loss_weights = {
        "cls": config["dataset"]["classification_loss_coef"],
        "charge": config["dataset"]["charge_loss_coef"],
        "pt": config["dataset"]["pt_loss_coef"],
        "eta": config["dataset"]["eta_loss_coef"],
        "sin_phi": config["dataset"]["sin_phi_loss_coef"],
        "cos_phi": config["dataset"]["cos_phi_loss_coef"],
        "energy": config["dataset"]["energy_loss_coef"],
    }
    return loss_dict, loss_weights
