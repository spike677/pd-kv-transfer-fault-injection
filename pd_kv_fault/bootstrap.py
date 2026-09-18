"""Defer patching until the native connector naturally imports.

Never import torch/Mooncake during Python site initialization.
"""
import importlib.abc
import importlib.machinery
import os
import sys
from .runtime import MODULE,Binding
from .control import Control

BINDING=None

def attach(module):
    global BINDING
    if BINDING is not None:raise RuntimeError('duplicate bootstrap')
    if os.environ.get('PD_FAULT_ENGINE_BASE'):
        from .identity import Settings
        from .scoped import ScopedControl
        c=ScopedControl(os.environ['PD_FAULT_DIR'],os.environ['PD_FAULT_OWNER'],Settings.from_env(),
                        getattr(module,'get_pp_group',None))
    else:
        c=Control(os.environ['PD_FAULT_DIR'],os.environ['PD_FAULT_OWNER'],os.environ['PD_FAULT_ENGINE'])
    BINDING=Binding(module,c)

class Loader(importlib.abc.Loader):
    def __init__(self,real):self.real=real
    def create_module(self,spec):return self.real.create_module(spec)
    def exec_module(self,module):self.real.exec_module(module);attach(module)

class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname!=MODULE:return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path)
        if spec is None or spec.loader is None:raise ImportError('native connector unavailable')
        spec.loader=Loader(spec.loader)
        return spec

def activate():
    if os.environ.get('PD_FAULT_ENABLE')!='1':return
    if os.environ.get('VLLM_SERVER_DEV_MODE')!='1' or os.environ.get('PD_FAULT_ROLE',os.environ.get('PD_FAULT_SIDE'))!='decode':
        raise RuntimeError('explicit developer Decode process required')
    keys=['PD_FAULT_DIR','PD_FAULT_OWNER']
    keys+=['PD_FAULT_DEPLOYMENT_ID','PD_FAULT_ENGINE_BASE','PD_FAULT_SESSION_ID'] if os.environ.get('PD_FAULT_ENGINE_BASE') else ['PD_FAULT_ENGINE']
    for key in keys:
        if not os.environ.get(key):raise RuntimeError('missing '+key)
    if MODULE in sys.modules:attach(sys.modules[MODULE])
    elif not any(isinstance(f,Finder) for f in sys.meta_path):sys.meta_path.insert(0,Finder())
