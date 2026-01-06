#!/usr/bin/env python3
import psutil
import time
import subprocess

def monitor_performance():
    print("=== Warehouse Picking System Performance Monitor ===")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # CPU and Memory
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    print(f"CPU Usage: {cpu_percent:.1f}%")
    print(f"Memory Usage: {memory.percent:.1f}% ({memory.used / (1024**3):.1f}GB / {memory.total / (1024**3):.1f}GB)")
    print()
    
    # Load average
    try:
        load1, load5, load15 = psutil.getloadavg()
        print(f"Load Average: {load1:.2f}, {load5:.2f}, {load15:.2f}")
    except:
        print("Load average: Not available")
    print()
    
    # Gunicorn processes
    gunicorn_processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'cmdline']):
        try:
            if 'gunicorn' in proc.info['name'] or (proc.info['cmdline'] and any('gunicorn' in arg for arg in proc.info['cmdline'])):
                gunicorn_processes.append(proc.info)
        except:
            continue
    
    print(f"Gunicorn Processes: {len(gunicorn_processes)}")
    for proc in gunicorn_processes:
        print(f"  PID {proc['pid']}: CPU {proc['cpu_percent']:.1f}%, Memory {proc['memory_percent']:.1f}%")
    print()
    
    print("=== End Monitor ===")

if __name__ == "__main__":
    monitor_performance()
