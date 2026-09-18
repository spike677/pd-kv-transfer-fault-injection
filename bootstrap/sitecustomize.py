import os
if os.environ.get('PD_FAULT_ENABLE')=='1':
    # Startup configuration errors must not silently leave an uninstrumented
    # service running (Python otherwise only prints sitecustomize exceptions).
    try:
        from pd_kv_fault.bootstrap import activate
        activate()
    except Exception:
        import traceback
        traceback.print_exc()
        os._exit(78)
