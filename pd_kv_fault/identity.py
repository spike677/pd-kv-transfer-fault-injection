"""Topology identity, resolved only from runtime facts or explicit settings."""
from dataclasses import asdict, dataclass
import os
import re
import socket


def engine_matches(base, runtime):
    return isinstance(runtime,str) and (runtime==base or
        re.fullmatch(re.escape(base)+r'-[0-9a-f]{32}',runtime) is not None)


def rank(value):
    return value if type(value) is int and value>=0 else None


@dataclass(frozen=True)
class Settings:
    deployment_id: str
    engine_base_id: str
    node_id: str
    session_id: str
    role: str='decode'
    node_id_source: str='explicit'
    dp_rank: int|None=None
    pp_rank: int|None=None

    @classmethod
    def from_env(cls):
        def optional(key):
            return rank(int(os.environ[key])) if key in os.environ else None
        return cls(os.environ['PD_FAULT_DEPLOYMENT_ID'],os.environ['PD_FAULT_ENGINE_BASE'],
            os.environ.get('PD_FAULT_NODE_ID') or socket.gethostname(),os.environ['PD_FAULT_SESSION_ID'],
            os.environ.get('PD_FAULT_ROLE','unknown'),
            'explicit' if os.environ.get('PD_FAULT_NODE_ID') else 'hostname_fallback',
            optional('PD_FAULT_DP_RANK'),optional('PD_FAULT_PP_RANK'))


def participant_id(deployment,engine,node,dp,pp,tp):
    return f'{deployment}/{engine}/{node}/dp{dp}/pp{pp}/tp{tp}'


def identify(receiver, settings, pp_getter=None):
    cfg=getattr(receiver,'vllm_config',None)
    kv=getattr(cfg,'kv_transfer_config',None)
    pc=getattr(cfg,'parallel_config',None)
    actual_role=getattr(kv,'kv_role',None)
    role=('decode' if actual_role=='kv_consumer' else actual_role) if actual_role is not None else settings.role
    # Explicit producer config always wins over a mistaken Decode environment.
    if settings.role!='decode':role=settings.role
    dp=rank(getattr(pc,'data_parallel_rank',None))
    dp_source='config' if dp is not None else 'unknown'
    if dp is None:dp=settings.dp_rank;dp_source='explicit_env' if dp is not None else 'unknown'
    pp=rank(getattr(receiver,'pp_rank',None));pp_source='receiver' if pp is not None else 'unknown'
    if pp is None and pp_getter is not None:
        try:pp=rank(pp_getter().rank_in_group);pp_source='runtime_group' if pp is not None else 'unknown'
        except (RuntimeError,AssertionError,AttributeError):pass
    if pp is None:pp=settings.pp_rank;pp_source='explicit_env' if pp is not None else 'unknown'
    tp=rank(getattr(receiver,'tp_rank',None));size=rank(getattr(receiver,'tp_size',None))
    return dict(deployment_id=settings.deployment_id,engine_base_id=settings.engine_base_id,
        runtime_engine_id=getattr(receiver,'local_engine_id',None),role=role,
        role_source='runtime_config' if actual_role is not None else 'explicit_env',
        node_id=settings.node_id,node_id_source=settings.node_id_source,hostname=socket.gethostname(),
        dp_rank=dp,pp_rank=pp,tp_rank=tp,tp_size=size,pid=os.getpid(),session_id=settings.session_id,
        dp_rank_source=dp_source,pp_rank_source=pp_source,
        participant_id=participant_id(settings.deployment_id,settings.engine_base_id,settings.node_id,dp,pp,tp))
