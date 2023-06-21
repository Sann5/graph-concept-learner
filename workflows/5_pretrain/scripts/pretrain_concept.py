#!/usr/bin/env python3
# from tqdm import tqdm
import sys
import os
import mlflow
from ruamel import yaml
import torch
from torch_geometric.loader import DataLoader
from torch_geometric import seed_everything
from graph_cl.models.gnn import GNN_plus_MPL
from graph_cl.datasets.concept_dataset import Concept_Dataset
from graph_cl.utils.mlflow_utils import robust_mlflow, start_mlflow_run
from graph_cl.utils.train_utils import (
    split_concept_dataset,
    get_optimizer_class,
    build_scheduler,
    test_and_log_best_models,
    train_validate_and_log_n_epochs,
    get_dict_of_metric_names_and_paths,
    randomize_labels,
)

### Debug input ###
# cfg = {
#     "pool": "global_mean_pool",
#     "gnn": "PNA",
#     "scaler": 2,
#     "num_layers": 4,
#     "dropout": False,
#     "act": "ReLU",
#     "act_first": False,
#     "norm": "LayerNorm",
#     "jk": "lstm",
#     "num_layers_MLP": 2,
#     "batch_size": 8,
#     "lr": 0.001,
#     "optim": "Adam",
#     "n_epoch": 100,
#     "scheduler":["ExponentialLR",0.98],
#     "seed": 25
# }
# pred_target = "ERStatus"
# concept_dataset_dir="/Users/ast/Documents/GitHub/datasets/jakson/prediction_tasks/ERStatus/processed_data/immune_radius"
# randomize="True"
# splits_df="/Users/ast/Downloads/sample_splits.csv"

# Read config file path
(
    prog_name,  # NAme of the script
    cfg_path,  # Path to config file
    splits_df,  # Path to df with data splits
    concept_dataset_dir,  # Path to dir with the concept dataset
    folder_name,  # Name of the folder
    split_strategy,  # Name of other folder
    run_type,  # Specify type of run
    randomize,  # Wehter to randomize the labes in the data
    pred_target,  # Prediction target
    root,  # Path to the dir with all the data (used to specify mlflow experiment)
    log_frequency,  # Frequency with which the model is loged
    out_file_1,  # Path to the output file with the final model
    out_file_2,  # Path to the output file with the final model
) = sys.argv

# Make out_dir if it does not already exist
out_dir = os.path.dirname(out_file_1)
os.makedirs(out_dir, exist_ok=True)

# Set torch device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Load config
with open(cfg_path) as file:
    cfg = yaml.load(file, Loader=yaml.Loader)

# Load dataset
dataset = Concept_Dataset(concept_dataset_dir)

# Save dataset infomration to config
cfg["num_classes"] = dataset.num_classes
cfg["in_channels"] = dataset.num_node_features
cfg["hidden_channels"] = cfg["in_channels"] * cfg["scaler"]

# Set seed
seed_everything(cfg["seed"])

# Get a separate dataset for each split
splited_datasets = split_concept_dataset(
    splits_df=splits_df, index_col="core", dataset=dataset
)

# Permute labels if fals is true
if bool(randomize):
    splited_datasets = randomize_labels(splits_df, pred_target, splited_datasets)

# Build model.
# Important to pass train_dataset in cpu, not cuda.
model = GNN_plus_MPL(cfg, splited_datasets["train"])

# Move to CUDA if available
model.to(device)

# Load datasets according to device
loaders = {}
for split_and_splited_dataset in splited_datasets.items():
    # Unpack key and value
    split, splited_dataset = split_and_splited_dataset
    loaders[split] = DataLoader(
        [data.to(device, non_blocking=True) for data in splited_dataset],
        batch_size=cfg["batch_size"],
        shuffle=True,
    )

# Define optimizer
optimizer_class = get_optimizer_class(cfg)
optimizer = optimizer_class(model.parameters(), lr=cfg["lr"])

# Define loss function.
criterion = torch.nn.CrossEntropyLoss()

# Define learning rate decay strategy
scheduler = build_scheduler(cfg, optimizer)

# Define mlflow experiment
start_mlflow_run(root, pred_target, out_dir)

# Add additional information to config st it is logged
cfg_file_name = os.path.basename(cfg_path)
cfg_id = os.path.splitext(cfg_file_name)[0]
cfg["run_type"] = run_type
cfg["folder_name"] = folder_name
cfg["split_strategy"] = split_strategy
cfg["cfg_id"] = cfg_id
cfg["concept"] = os.path.basename(concept_dataset_dir)
cfg["path_input_config"] = cfg_path
cfg["path_output_models"] = out_dir
if cfg["gnn"] == "PNA":
    cfg.pop("deg")

# Log config
robust_mlflow(mlflow.log_params, params=cfg)

### Training and evaluation ###
# Log frequency in terms of epochs
log_every_n_epochs = int(log_frequency)

# Save checkpoints for the follwoing metrics
follow_this_metrics = get_dict_of_metric_names_and_paths(out_file_1, out_file_2)

# Train and validate for cfg["n_epochs"]
# for epoch in tqdm(range(1, cfg["n_epoch"] + 1)):  # this line if for debugging
train_validate_and_log_n_epochs(
    cfg=cfg,
    model=model,
    train_loader=loaders["train"],
    val_loader=loaders["val"],
    criterion=criterion,
    optimizer=optimizer,
    scheduler=scheduler,
    log_every_n_epochs=log_every_n_epochs,
    device=device,
    follow_this_metrics=follow_this_metrics,
)

### Load best models an compute test metrics ###
test_and_log_best_models(
    cfg=cfg,
    model=model,
    test_loader=loaders["test"],
    criterion=criterion,
    device=device,
    follow_this_metrics=follow_this_metrics,
    out_dir=out_dir,
    split="test",
)

# Test external_test if present
if "external_test" in loaders.keys():
    test_and_log_best_models(
        cfg=cfg,
        model=model,
        test_loader=loaders["external_test"],
        criterion=criterion,
        device=device,
        follow_this_metrics=follow_this_metrics,
        out_dir=out_dir,
        split="external_test",
    )

# End run
mlflow.end_run()