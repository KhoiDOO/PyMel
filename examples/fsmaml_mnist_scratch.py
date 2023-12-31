import os, sys
sys.path.append("/".join(os.path.dirname(__file__).split("/")[:-1]))
import argparse
import copy
import random

import torch
from torch import nn
from torch.optim import Adam
from torch.utils.data import DataLoader
from torchvision import transforms

from pymel import MamlMnist
from pymel.dataset.utils import single_task_detach
from pymel.base_model import CNN_Mnist

def main(args: argparse):
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu", index=args.dv)

    torch.backends.cudnn.benchmark = True
    
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,))
    ])
    
    train_ds = MamlMnist(
        root="~/data",
        train = True,
        transform=transform,
        download=True,
        k_shot=args.ks,
        k_query=args.kq
    )
    
    test_ds = MamlMnist(
        root="~/data",
        train = False,
        transform=transform,
        download=True,
        maml=False
    )
    
    train_dl = DataLoader(
        dataset=train_ds,
        batch_size=args.ks + args.kq,
        num_workers=args.wk, 
        pin_memory=True,
        shuffle=True
    )
    
    test_dl = DataLoader(
        dataset=test_ds,
        batch_size=1,
        num_workers=args.wk, 
        pin_memory=True
    )
    
    global_model = CNN_Mnist(input_size=(1, 28, 28), num_classes=10).to(device=device)

    meta_optimizer = Adam(global_model.parameters(), lr=args.out_lr, weight_decay=1e-4)

    criterion = nn.CrossEntropyLoss()

    num_task = train_ds.nt
    for epoch in range(args.epochs):
        global_model.train()
        
        for train_idx, data_dict in enumerate(train_dl):
            
            metaloss = 0.0
            for task in data_dict:
                task_model = copy.deepcopy(global_model)
                task_optimizer = Adam(task_model.parameters(), lr=args.in_lr, weight_decay=1e-4)
                
                sp_x, sp_y, qr_x, qr_y = single_task_detach(
                    batch_dict=data_dict,
                    k_shot=args.ks,
                    k_query=args.kq,
                    task=task
                )
                
                for in_e in range(args.inner_epochs):
                    sp_x, sp_y = sp_x.to(device), sp_y.to(device)
                    sp_logits = task_model(sp_x)
                    sp_loss = criterion(sp_logits, sp_y)
                    task_optimizer.zero_grad()
                    sp_loss.backward()
                    task_optimizer.step()
                
                qr_x, qr_y = qr_x.to(device), qr_y.to(device)
                qr_logits = task_model(qr_x)
                qr_loss = criterion(qr_logits, qr_y)
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
                test_imgs = test_imgs.to(device, non_blocking=True)
                test_labels = test_labels.to(device, non_blocking=True)
                test_logits = global_model(test_imgs)                
            
                test_loss += criterion(test_logits, test_labels).item()
                _, predicted = test_logits.max(1)
                total += test_labels.size(0)
                correct += predicted.eq(test_labels).sum().item()
                
        print(f"Epoch: {epoch} - MetaLoss: {metaloss/num_task} - Test Loss: {test_loss/batch_count} - Test Acc: {100*correct/total}%")   
        

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="MINST MULTI TASK CLASSIFICATION"
    )
    
    parser.add_argument("--in_lr", type=float, default=0.01,
                        help="Inner learning Rate")
    parser.add_argument("--out_lr", type=float, default=0.001,
                        help="Outer learning Rate")
    parser.add_argument("--ks", type=int, default=5,
                        help="#sample in support set")
    parser.add_argument("--kq", type=int, default=5,
                        help="#sample in query set")
    parser.add_argument("--wk", type=int, default=os.cpu_count(),
                        help="#number of workers")
    parser.add_argument("--c", type=int, default=1,
                        help="#number of imput channel")
    parser.add_argument("--epochs", type=int, default=1,
                        help="#number of epochs")
    parser.add_argument("--inner_epochs", type=int, default=1,
                        help="#number of epochs")
    parser.add_argument("--dv", type=int, default=0,
                        help="Index of GPU")
    
    args = parser.parse_args()
    
    main(args=args)