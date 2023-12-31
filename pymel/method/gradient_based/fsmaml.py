import os, sys
sys.path.append("/".join(os.path.dirname(__file__).split("/")[:-1]))
from typing import *
from random import randint
import argparse
from tqdm import tqdm
import copy

from pymel.config import DSConfig, TrainConfig
from core import Trainer, opt_mapping
from dataset.utils import single_task_detach

import torch
from torch import nn
import torch.multiprocessing as mp
import torch.distributed as dist
from torch.utils.data import DataLoader
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data.distributed import DistributedSampler


class FSMAML(Trainer):
    def __init__(self, ds_cfg: DSConfig, tr_cfg: TrainConfig, 
                 model: nn.Module = None, gpus: List[int] = ...,
                 meta_opt: str = None, 
                 meta_lr: float = 0.001,
                 meta_wd: float = 1e-4,
                 sp_opt: str = None,
                 sp_lr: float = 0.01,
                 sp_wd: float = 1e-4,
                 criterion: torch.nn.Module = nn.CrossEntropyLoss(),
                 outer_epoch: int = 100,
                 inner_epoch: int = 1
                 ) -> None:
        super().__init__(ds_cfg, tr_cfg, model, gpus)
        
        os.environ["CUDA_VISIBLE_DEVICES"] = ",".join([str(x) for x in gpus])
        
        self.ds_cfg = ds_cfg
        self.tr_cfg = tr_cfg
        self.model = model
        self.gpus = gpus
        
        for optn, opt in zip(["meta_opt", "sp_opt"], [meta_opt, sp_opt]):
            if not isinstance(opt, str):
                raise TypeError(f"PyMel GPT: {optn} must be a string, \
                    but found {type(opt)} instead")
            elif opt is None:
                raise ValueError(f"PyMel GPT: {optn} cannot be a None")
        
        for name, value in zip(
            ["meta_lr", "meta_wd", "sp_lr", "sp_wd"],
            [meta_lr, meta_wd, sp_lr, sp_wd]
        ):
            if not isinstance(value, float):
                raise TypeError(f"PyMel GPT: {name} must be a float, \
                    but found {type(value)} instead")
        
        if not isinstance(criterion, torch.nn.Module):
            raise TypeError(f"PyMel GPT: {criterion} must be a float, \
                but found {type(criterion)} instead")
        
        for name, var in zip(
            ["outer_epoch", "inner_epoch"],
            [outer_epoch, inner_epoch]
        ):
            if not isinstance(var, int):
                raise TypeError(f"PyMel GPT: {name} must be an int, \
                        but found {type(var)} instead")
        
        self.meta_opt = meta_opt
        self.sp_opt = sp_opt
        self.meta_lr = meta_lr
        self.meta_wd = meta_wd
        self.sp_lr = sp_lr
        self.sp_wd = sp_wd
        self.crit = criterion        
        self.outer_epoch = outer_epoch
        self.inner_epoch = inner_epoch
        self.method = "fsmaml"
        self.tr_cfg.folder_setup(
            method=self.method, 
            dataset=self.ds_cfg.config["dataset"],
            k_shot=self.ds_cfg.get_k_shot(),
            k_query=self.ds_cfg.get_k_query()
        )
    
    def single_train(self):
        
        dsn = self.ds_cfg.config["dataset"]
        ks = self.ds_cfg.get_k_shot()
        kq = self.ds_cfg.get_k_query()
        print(f"Method: {self.method} - Dataset: {dsn} - ks: {ks} - kq: {kq}")
        
        batch_size = ks + kq
        train_dl = DataLoader(
            dataset=self.ds_cfg.train_ds, 
            batch_size=batch_size, 
            num_workers=self.ds_cfg.get_wk(), 
            pin_memory=self.ds_cfg.get_pin_mem(), 
        )
        
        test_dl = DataLoader(
            dataset=self.ds_cfg.test_ds,
            batch_size=1,
            num_workers=self.ds_cfg.get_wk(), 
            pin_memory=self.ds_cfg.get_pin_mem(),
        )
        
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu", index=self.gpus[0])
        
        global_model = self.model.to(device)
        
        meta_optimizer = opt_mapping[self.meta_opt](
            global_model.parameters(), 
            lr=self.meta_lr, weight_decay=self.meta_wd
        )
        
        num_task = self.ds_cfg.train_ds.nt
        for epoch in range(self.outer_epoch):
            global_model.train()
            
            for train_idx, data_dict in enumerate(train_dl):
                
                metaloss = 0.0
                for task in data_dict:
                    task_model = copy.deepcopy(global_model)
                    task_optimizer = opt_mapping[self.meta_opt](
                        task_model.parameters(), 
                        lr=self.sp_lr, weight_decay=self.sp_wd
                    )
                    
                    sp_x, sp_y, qr_x, qr_y = single_task_detach(
                        batch_dict=data_dict,
                        k_shot=self.ds_cfg.get_k_shot(),
                        k_query=self.ds_cfg.get_k_query(),
                        task=task
                    )
                    
                    for in_e in range(self.inner_epoch):
                        sp_x, sp_y = sp_x.to(device), sp_y.to(device)
                        sp_logits = task_model(sp_x)
                        sp_loss = self.crit(sp_logits, sp_y)
                        task_optimizer.zero_grad()
                        sp_loss.backward()
                        task_optimizer.step()
                    
                    qr_x, qr_y = qr_x.to(device), qr_y.to(device)
                    qr_logits = task_model(qr_x)
                    qr_loss = self.crit(qr_logits, qr_y)
                    metaloss += qr_loss.item()
                    qr_loss.backward()            

                    for w_global, w_local in zip(global_model.parameters(), task_model.parameters()):
                        if w_global.grad is None:
                            w_global.grad = w_local.grad
                        else:
                            w_global.grad += w_local.grad

                meta_optimizer.step()
                meta_optimizer.zero_grad()            
            
            global_model.eval()
            with torch.no_grad():
                test_loss = 0
                correct = 0
                total = 0
                batch_count = 0
                for test_idx, (test_imgs, test_labels) in enumerate(test_dl):
                    batch_count = test_idx
                    test_imgs = test_imgs.to(device)
                    test_labels = test_labels.to(device)
                    test_logits = global_model(test_imgs)                
                
                    test_loss += self.crit(test_logits, test_labels).item()
                    _, predicted = test_logits.max(1)
                    total += test_labels.size(0)
                    correct += predicted.eq(test_labels).sum().item()
                    
            print(f"Epoch: {epoch} - MetaLoss: {metaloss/num_task} - Test Loss: {test_loss/batch_count} - Test Acc: {100*correct/total}%")  
        
    def train(self, port=randint(1000, 8000)):
        raise NotImplementedError()
        parser = argparse.ArgumentParser()
        args = parser.parse_args()
            
        args.rank = 0
        args.port = port
        args.dist_url = f'tcp://localhost:{port}'
        print(f"PyMel GPT: The experiment is deployed at {args.dist_url}")
        
        args.world_size = torch.cuda.device_count()
        
        mp.spawn(self.main_worker, (args,), nprocs = args.world_size)
        
    def main_worker(self, gpu, args):
        print("get here")
        args.rank += gpu

        dist.init_process_group(
            backend='nccl', 
            init_method=args.dist_url,
            world_size=args.world_size, rank=args.rank)
        
        torch.cuda.set_device(gpu)
        torch.backends.cudnn.benchmark = True
        print("Process Group Init: Done")
        
        batch_size = self.ds_cfg.get_k_shot() + self.ds_cfg.get_k_query()
        assert batch_size % args.world_size == 0
        assert self.ds_cfg.get_k_shot() % args.world_size == 0
        assert self.ds_cfg.get_k_query() % args.world_size == 0
        
        train_sampler = DistributedSampler(self.ds_cfg.train_ds)
        test_sampler = DistributedSampler(self.ds_cfg.test_ds)
        
        per_device_batch_size = batch_size // args.world_size
        per_device_k_shot = self.ds_cfg.get_k_shot() // args.world_size
        per_device_k_query = self.ds_cfg.get_k_query() // args.world_size
        
        train_dl = DataLoader(
            dataset=self.ds_cfg.train_ds, 
            batch_size=per_device_batch_size, 
            num_workers=self.ds_cfg.get_wk(), 
            pin_memory=self.ds_cfg.get_pin_mem(), 
            sampler=train_sampler
        )
        
        test_dl = DataLoader(
            dataset=self.ds_cfg.test_ds,
            batch_size=1,
            num_workers=self.ds_cfg.get_wk(), 
            pin_memory=self.ds_cfg.get_pin_mem(),
            sampler=test_sampler
        )
        print("Data Loader Setup: Done")
        
        global_model = self.model.cuda(gpu)
        global_model = nn.SyncBatchNorm.convert_sync_batchnorm(global_model)
        global_model = torch.compile(model=global_model)
        global_model = DDP(global_model, device_ids=[gpu])
        
        print("Setup Model: Done")
        
        meta_optimizer = opt_mapping[self.meta_opt](
            global_model.parameters(), 
            lr=self.meta_lr, weight_decay=self.meta_wd
        )
        
        num_task = self.ds_cfg.train_ds.nt
        for epoch in range(self.outer_epoch):
            train_sampler.set_epoch(epoch)
            global_model.train()
            
            for train_idx, data_dict in enumerate(train_dl):
                
                metaloss = 0.0
                for task in data_dict:
                    task_model = copy.deepcopy(global_model)
                    task_optimizer = opt_mapping[self.meta_opt](
                        task_model.parameters(), 
                        lr=self.sp_lr, weight_decay=self.sp_wd
                    )
                    
                    sp_x, sp_y, qr_x, qr_y = single_task_detach(
                        batch_dict=data_dict,
                        k_shot=per_device_k_shot,
                        k_query=per_device_k_query,
                        task=task
                    )
                    
                    for in_e in range(self.inner_epochs):
                        sp_x, sp_y = sp_x.cuda(gpu), sp_y.cuda(gpu)
                        sp_logits = task_model(sp_x)
                        sp_loss = self.crit(sp_logits, sp_y)
                        task_optimizer.zero_grad()
                        sp_loss.backward()
                        task_optimizer.step()
                    
                    qr_x, qr_y = qr_x.cuda(gpu), qr_y.cuda(gpu)
                    qr_logits = task_model(qr_x)
                    qr_loss = self.crit(qr_logits, qr_y)
                    metaloss += qr_loss.item()
                    qr_loss.backward()            

                    for w_global, w_local in zip(global_model.parameters(), task_model.parameters()):
                        if w_global.grad is None:
                            w_global.grad = w_local.grad
                        else:
                            w_global.grad += w_local.grad

                meta_optimizer.step()
                meta_optimizer.zero_grad()
            if args.rank == 0:
                test_sampler.set_epoch(epoch)
                global_model.eval()
                with torch.no_grad():
                    test_loss = 0
                    correct = 0
                    total = 0
                    batch_count = 0
                    for test_idx, (test_imgs, test_labels) in enumerate(test_dl):
                        batch_count = test_idx
                        test_imgs = test_imgs.cuda(gpu)
                        test_labels = test_labels.cuda(gpu)
                        test_logits = global_model(test_imgs)                
                    
                        test_loss += self.crit(test_logits, test_labels).item()
                        _, predicted = test_logits.max(1)
                        total += test_labels.size(0)
                        correct += predicted.eq(test_labels).sum().item()
                        
                print(f"Epoch: {epoch} - MetaLoss: {metaloss/num_task} - Test Loss: {test_loss/batch_count} - Test Acc: {100*correct/total}%")  
    
        dist.destroy_process_group()