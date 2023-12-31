import os, sys
from typing import *
import torch
import random

def detach(
    batch_dict: Dict[int, List[torch.Tensor]] = None, 
    k_shot:int = None, 
    k_query:int = None
    ) -> Tuple[Dict[int, List[torch.Tensor]]]:
    sample_len = len(batch_dict[list(batch_dict.keys())[0]])
    
    if k_shot + k_query > sample_len:
        raise ValueError(f"Many data to unpack. Since #sample in support set: k_shot and #sample \
            in query set k_query must satisfy the condition: k_shot + k_query == #sample \
                in a batch per task.")
    elif k_shot + k_query < sample_len:
        raise UserWarning(f"the #sample in support set: k_shot and #sample in query set: k_query \
            totally are less than the #sample available in batch task dict. The redundant samples are \
                used in automatically used in query set.")
    
    support_dct = {
        _cls : batch_dict[_cls][:k_shot] for _cls in batch_dict
    }
    
    query_dct = {
        _cls : batch_dict[_cls][k_shot:] for _cls in batch_dict
    }
    
    return (support_dct, query_dct)

def maml_detach(
    batch_dict: Dict[int, List[torch.Tensor]] = None, 
    k_shot:int = None, 
    k_query:int = None,
    task:int = None
    ) -> Tuple[torch.Tensor]:
    
    support_dct, query_dct = detach(
        batch_dict=batch_dict,
        k_shot=k_shot,
        k_query=k_query
    )
    
    if not isinstance(task, int):
        raise ValueError(f"task arg must be integer type but found {type(task)} instead")
    elif task not in batch_dict.keys():
        raise Exception(f"Found no task {task} in batch dict")
    
    tasks = list(batch_dict.keys())
    
    support_x, support_y, query_x, query_y = [], [], [], []
    for _task in tasks:
        support_x.extend(support_dct[_task])
        query_x.extend(query_dct[_task])
        if _task == task:
            support_y.extend([1]*k_shot)
            query_y.extend([1]*k_query)
        else:
            support_y.extend([0]*k_shot)
            query_y.extend([0]*k_query)
            
    # shuf_sp_lst, shuf_qr_lst = list(range(len(support_x))), list(range(len(query_x)))
    # random.shuffle(shuf_sp_lst)
    # random.shuffle(shuf_qr_lst)
    
    # support_x = torch.stack(support_x)[torch.tensor(shuf_sp_lst)]
    # support_y = torch.FloatTensor(support_y)[torch.tensor(shuf_sp_lst)]
    # query_x = torch.stack(query_x)[torch.tensor(shuf_qr_lst)]
    # query_y = torch.FloatTensor(query_y)[torch.tensor(shuf_qr_lst)]
    
    support_x = torch.stack(support_x)
    support_y = torch.FloatTensor(support_y)
    query_x = torch.stack(query_x)
    query_y = torch.FloatTensor(query_y)
    
    return (support_x, support_y, query_x, query_y)

def single_task_detach(
    batch_dict: Dict[int, List[torch.Tensor]] = None, 
    k_shot:int = None, 
    k_query:int = None,
    task:int = None
    ):
    
    support_dct, query_dct = detach(
        batch_dict=batch_dict,
        k_shot=k_shot,
        k_query=k_query
    )
    
    if not isinstance(task, int):
        raise ValueError(f"task arg must be integer type but found {type(task)} instead")
    elif task not in batch_dict.keys():
        raise Exception(f"Found no task {task} in batch dict")
    
    support_x, support_y, query_x, query_y = [], [], [], []
    
    support_x.extend(support_dct[task])
    support_y.extend([task]*len(support_dct[task]))
    query_x.extend(query_dct[task])
    query_y.extend([task]*len(query_dct[task]))
    
    support_x = torch.stack(support_x)
    support_y = torch.LongTensor(support_y)
    query_x = torch.stack(query_x)
    query_y = torch.LongTensor(query_y)
    
    return (support_x, support_y, query_x, query_y)