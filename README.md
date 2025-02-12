This project implements a RESTful API to [PyTrain](https://github.com/cdswindell/PyLegacy). Via the API, you can
control and operate trains, switches, accessories, and any other equipment that use Lionel's Legacy/TMCC command
protocol. **PyTrain Api** is used by the **PyTrain** Alexa skill to enables voice-control of your layout.

**PyTrain Api** is developed in pure Python. It uses the [FastAPI](https://fastapi.tiangolo.com) framework and
includes an ASGI-compliant web server, [Uvicorn](https://www.uvicorn.org). Once installed, it only takes one
command to launch the **PyTrain Api** and begin serving requests.

**PyTrain Api** runs as a **PyTrain** client connected to another **PyTrain** server, or can act as both
a **PyTrain** server _and_ a **PyTrain Api** server. And, like **PyTrain**, **PyTrain Api** can run on a Raspberry Pi
running 64 bit Bookworm distribution or later.