"""Trainining script for Tacotron speech synthesis model.

usage: train.py [options]

options:
    --conf=<json>             Path of configuration file (json).
    --gpu-id=<N>               ID of the GPU to use [default: 0]
    --exp-dir=<dir>           Experiment directory
    --checkpoint-dir=<dir>    Directory where to save model checkpoints [default: checkpoints].
    --checkpoint-path=<name>  Restore model from checkpoint path if given.
    --hparams=<parmas>        Hyper parameters [default: ].
    --log-event-path=<dir>    Log Path [default: exp/log_tacotronOne]
    -h, --help                Show this help message and exit
"""
import os, sys
from docopt import docopt
args = docopt(__doc__)
print("Command line args:\n", args)
gpu_id = args['--gpu-id']
print("Using GPU ", gpu_id)
os.environ["CUDA_VISIBLE_DEVICES"]=gpu_id


from collections import defaultdict

### This is not supposed to be hardcoded #####
FALCON_DIR = os.environ.get('FALCONDIR')
sys.path.append(FALCON_DIR)
##############################################
from utils.misc import *
from utils import audio
from utils.plot import plot_alignment
from tqdm import tqdm, trange
from util import *
from model import ValenceSeq2Seq_DownsamplingEncoder

import json

import torch
from torch.utils import data as data_utils
from torch.autograd import Variable
from torch import nn
from torch import optim
import torch.backends.cudnn as cudnn
from torch.utils import data as data_utils
import numpy as np

from os.path import join, expanduser

import tensorboard_logger
from tensorboard_logger import *
from hyperparameters import hparams, hparams_debug_string

vox_dir ='vox'

global_step = 0
global_epoch = 0
use_cuda = torch.cuda.is_available()
if use_cuda:
    cudnn.benchmark = False
use_multigpu = None

fs = hparams.sample_rate



def validate_model_full(model, val_loader):
     print("Validating the model")
     model.eval()
     y_true = []
     y_pred = []
     with torch.no_grad():
      for step, (x, mel) in enumerate(val_loader):
          #print("Shape of input during validation: ", x.shape, mel.shape)    
          x, mel = Variable(x).cuda(), Variable(mel).cuda()
          logits = model(mel)
          targets = x.cpu().view(-1).numpy()
          y_true += targets.tolist()
          predictions = return_classes(logits) 
          y_pred += predictions.tolist()  
          #print(predictions, targets)
     #print(y_pred, y_true)
     recall = get_metrics(y_pred, y_true)
     print("Unweighted Recall for the validation set:  ", recall)
     print('\n')
     return recall

def validate_model(model, val_loader):
     print("Validating the model")
     model.eval()
     y_true = []
     y_pred = []
     with torch.no_grad():
      for step, (x, mel) in enumerate(val_loader):
       if step < 15: 
          #print("Shape of input during validation: ", x.shape, mel.shape)    
          x, mel = Variable(x).cuda(), Variable(mel).cuda()
          logits = model(mel)
          targets = x.cpu().view(-1).numpy()
          y_true += targets.tolist()
          predictions = return_classes(logits) 
          y_pred += predictions.tolist()  
          #print(predictions, targets)
     #print(y_pred, y_true)
     recall = get_metrics(y_pred, y_true)
     print("Unweighted Recall for the validation set:  ", recall)
     print('\n')
     return recall

def train(model, train_loader, val_loader, optimizer,
          init_lr=0.002,
          checkpoint_dir=None, checkpoint_interval=None, nepochs=None,
          clip_thresh=1.0):
    if use_cuda:
        model = model.cuda()

    criterion = nn.CrossEntropyLoss()
    global global_step, global_epoch
    while global_epoch < nepochs:
        #validate_model(model, val_loader)
        model.train()
        h = open(logfile_name, 'a')
        running_loss = 0.
        for step, (x, mel) in tqdm(enumerate(train_loader)):

            # Decay learning rate
            current_lr = learning_rate_decay(init_lr, global_step)
            for param_group in optimizer.param_groups:
                param_group['lr'] = current_lr

            optimizer.zero_grad()

            # Feed data
            x, mel = Variable(x), Variable(mel)
            if use_cuda:
                x, mel = x.cuda(), mel.cuda()

            val_outputs = model(mel)

            # Loss
            loss = criterion(val_outputs, x)

            # Update
            loss.backward(retain_graph=False)
            grad_norm = torch.nn.utils.clip_grad_norm_(
                 model.parameters(), clip_thresh)
            optimizer.step()

            # Logs
            log_value("loss", float(loss.item()), global_step)
            log_value("gradient norm", grad_norm, global_step)
            log_value("learning rate", current_lr, global_step)
            global_step += 1
            running_loss += loss.item()

        averaged_loss = running_loss / (len(train_loader))
        log_value("loss (per epoch)", averaged_loss, global_epoch)
        h.write("Loss after epoch " + str(global_epoch) + ': '  + format(running_loss / (len(train_loader))) + '\n')
        h.close()
        recall = validate_model(model, val_loader)
        log_value("Unweighted Recall per epoch", recall, global_epoch)

        global_epoch += 1


if __name__ == "__main__":

    exp_dir = args["--exp-dir"]
    checkpoint_dir = args["--exp-dir"] + '/checkpoints'
    checkpoint_path = args["--checkpoint-path"]
    log_path = args["--exp-dir"] + '/tracking'
    conf = args["--conf"]
    hparams.parse(args["--hparams"])

    # Override hyper parameters
    if conf is not None:
        with open(conf) as f:
            hparams.parse_json(f.read())

    os.makedirs(exp_dir, exist_ok=True)
    os.makedirs(checkpoint_dir, exist_ok=True)
    os.makedirs(log_path, exist_ok=True)
    logfile_name = log_path + '/logfile'
    h = open(logfile_name, 'w')
    h.close()


    feats_name = 'valence'
    X_train = categorical_datasource( vox_dir + '/' + 'fnames.train', vox_dir + '/' + 'etc/falcon_feats.desc', feats_name, vox_dir + '/' +  'festival/falcon_' + feats_name)
    X_val = categorical_datasource(vox_dir + '/' +  'fnames.val', vox_dir + '/' +  'etc/falcon_feats.desc', feats_name,  vox_dir + '/' +  'festival/falcon_' + feats_name)

    feats_name = 'mspec'
    Mel_train = float_datasource(vox_dir + '/' + 'fnames.train', vox_dir + '/' + 'etc/falcon_feats.desc', feats_name, vox_dir + '/' + 'festival/falcon_' + feats_name)
    Mel_val = float_datasource(vox_dir + '/' + 'fnames.val', vox_dir + '/' + 'etc/falcon_feats.desc', feats_name, vox_dir + '/' + 'festival/falcon_' + feats_name)

    # Dataset and Dataloader setup
    trainset = ValenceDataset(X_train, Mel_train)
    train_loader = data_utils.DataLoader(
        trainset, batch_size=hparams.batch_size,
        num_workers=hparams.num_workers, shuffle=True,
        collate_fn=collate_fn_valence, pin_memory=hparams.pin_memory)

    valset = ValenceDataset(X_val, Mel_val)
    val_loader = data_utils.DataLoader(
        valset, batch_size=hparams.batch_size,
        num_workers=hparams.num_workers, shuffle=True,
        collate_fn=collate_fn_valence, pin_memory=hparams.pin_memory)

    # Model
    model = ValenceSeq2Seq_DownsamplingEncoder(39)
    model = model.cuda()

    optimizer = optim.Adam(model.parameters(),
                           lr=hparams.initial_learning_rate, betas=(
                               hparams.adam_beta1, hparams.adam_beta2),
                           weight_decay=hparams.weight_decay)

    # Load checkpoint
    if checkpoint_path:
        print("Load checkpoint from: {}".format(checkpoint_path))
        checkpoint = torch.load(checkpoint_path)
        model.load_state_dict(checkpoint["state_dict"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        try:
            global_step = checkpoint["global_step"]
            global_epoch = checkpoint["global_epoch"]
        except:
            # TODO
            pass

    # Setup tensorboard logger
    tensorboard_logger.configure(log_path)

    print(hparams_debug_string())

    # Train!
    try:
        train(model, train_loader, val_loader, optimizer,
              init_lr=hparams.initial_learning_rate,
              checkpoint_dir=checkpoint_dir,
              checkpoint_interval=hparams.checkpoint_interval,
              nepochs=hparams.nepochs,
              clip_thresh=hparams.clip_thresh)
        validate_model_full(model, val_loader)
        save_checkpoint(
            model, optimizer, global_step, checkpoint_dir, global_epoch)

    except KeyboardInterrupt:
        save_checkpoint(
            model, optimizer, global_step, checkpoint_dir, global_epoch)

    print("Finished")
    sys.exit(0)


