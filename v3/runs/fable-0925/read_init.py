import sys, time, json
t=time.monotonic()
mode = sys.argv[1]
if mode == "text":
    for line in sys.stdin:
        break
elif mode == "buffer":
    line = sys.stdin.buffer.readline().decode("utf-8")
t1=time.monotonic()-t
t=time.monotonic(); msg=json.loads(line); t2=time.monotonic()-t
t=time.monotonic(); msg["payload"].pop("target_catalog"); import gc; gc.collect(); t3=time.monotonic()-t
print(f"{sys.executable.split('/')[-3] if 'py312np' in sys.executable else sys.executable.split('/')[-1]} {mode}: readline {t1:.1f}s, json.loads {t2:.1f}s, pop+gc {t3:.2f}s, total {t1+t2:.1f}s")
