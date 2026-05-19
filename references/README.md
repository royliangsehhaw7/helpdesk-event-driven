# 
## 1 Definitions
### 1.1 Coroutines
- **Special Functions**: They are Python **functions** defined using the async def syntax instead of just def.
- **Single-Threaded**: Multiple coroutines execute sequentially inside one single thread, meaning they do not run in true parallel across multiple CPU cores.
- 

## 2. Threads
- Threads are used for concurrency, allowing multiple parts of a program to run at the same time. 
- They share the same memory space, making it easy to share data but requiring careful management to avoid conflicts.
- Best for: I/O-bound tasks like downloading files or querying a database.
- The Python Interpreter (specifically the CPython runtime) handles the pausing and switching, working closely with your Operating System (OS) [1].

>[!NOTE]
>There are multiple threads, but only one thread can execute code at any single microsecond.


---

## 3. References
- https://realpython.com/intro-to-python-threading/
- https://medium.com/@speaktoharisudhan/threading-in-python-dba88fe8a4d8

---

## 4. Samples
### 3.1
```python
import threading
import time

def task(name):
    print(f"Task {name} starting")
    time.sleep(2)  # This simulates waiting for I/O
    print(f"Task {name} finished")

# Create two threads
t1 = threading.Thread(target=task, args=("A",))
t2 = threading.Thread(target=task, args=("B",))

# Start them (Python and the OS automatically handle the switching here)
t1.start()
t2.start()

t1.join()
t2.join()

```
