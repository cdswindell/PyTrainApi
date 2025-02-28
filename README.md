This project implements a RESTful API to [PyTrain](https://github.com/cdswindell/PyLegacy). Via the API, you can
control and operate trains, switches, accessories, and any other equipment that use Lionel's Legacy/TMCC command
protocol. **PyTrain Api** is used by the **PyTrain** Alexa skill to enables voice-control of your layout.

**PyTrain Api** is developed in pure Python. It uses the [FastAPI](https://fastapi.tiangolo.com) framework and
includes an ASGI-compliant web server, [Uvicorn](https://www.uvicorn.org). Once installed, it only takes one
command to launch the **PyTrain Api** and begin serving requests.

**PyTrain Api** runs as a **PyTrain** client connected to another **PyTrain** server, or can act as both
a **PyTrain** server _and_ a **PyTrain Api** server. And, like **PyTrain**, **PyTrain Api** can run on a Raspberry Pi
running 64 bit Bookworm distribution or later.

## Quick Start

### Requirements

Minimum requirements to use **PyTrain API** are:

* A Lionel Base 3 running the most current Lionel firmware and configured in *infrastructure* mode
* A network-capable Raspberry Pi 4 or 5 running Raspberry PI OS 64-bit Bookworm
* A Mac or Windows computer to set up the Raspberry Pi(s)
* All hardware connected to the same Wi-Fi network
* Python 3.10 - 3.12 installed (Python 3.11 is standard with the Bookworm release of Raspberry Pi OS)
* Internet access (to download software)

Recommended:

* A **PyTrain** server running on a separate Raspberry Pi 4 or 5 Wi-Fi-equipped computer with at least 2 GB
  of RAM running Raspberry PI OS 64-bit Bookworm

Notes:

* It is recommended to have a Lionel LCS Ser2 module connected to your **PyTrain** server, as
  the Lionel Base 3 **_does not_** broadcast all layout activity
* **PyTrain** is a command-line tool. It must be run from a Terminal window (macOS/Linux/Pi) or a Cmd
  shell (Windows). **PyTrain** does _not_ have a GUI nor run as a native app.
* **PyTrain** _may_ work with an LCS Wi-Fi module, but this configuration hasn't been tested
* The **PyTrain** CLI can be run on a Mac or Windows system. It allows complete control of _all_ TMCC or
  Legacy-equipped devices as well as allows you to monitor all TMCC and Legacy commands

### Installation

**PyTrain Api** can be run directly from a Python virtual environment or
from a **PyTrain Api** development environment, The former is preferable if you do not plan
on changing the **PyTrain Api** code itself. Both methods require the creation of a
Python [virtual environment](https://developer.vonage.com/en/blog/a-comprehensive-guide-on-working-with-python-virtual-environments#using-venv).

#### Create a Python Virtual Environment

* Open a Terminal shell window and navigate to the folder/directory where
  you will install __PyTrain Api__
* Create a virtual environment with the command:

```aiignore
python3 -m venv PyTrainApiEnv
```

* In the same terminal window, `cd` into the directory you created above and activate the environment:

```aiignore
cd PyTrainApiEnv
source bin/activate
```

#### Run __PyTrain Api__ Directly

* Use `pip` to install the __PyTrain Api__ package from `pypi`:

```aiignore
pip3 install pytrain-ogr-api
```

* Run the __PyTrain Api__ server:

```aiignore
pytrain_api
```

This command launches a webserver that accepts requests on port `8000`and connects to a __PyTrain__ server.
To see the list of available Api endpoints, open a web browser and go to `http://<your local IP>:8000/pytrain`.

#### Run __PyTrain Api__ from a Development Environment

* In the same terminal window where you created the virtual environment, `cd` into the directory you
  created above and download the __PyTrain Api__ source code (this assumes you have installed
  the [GitHub CLI](https://cli.github.com)):

```aiignore
gh repo clone cdswindell/PyTrainApi
```

* `cd` into the directory `PyTrainApi` and activate the environment:

```aiignore
cd PyTrainApi
source ../bin/activate
```

* Install the __PyTrain Api__ required packages:

```aiignore
pip3 install -r requirements.txt
```

* Run the __PyTrain Api__ server:

```aiignore
cli/pytrain_api
```

This command launches a webserver that accepts requests on port `8000`and connects to a __PyTrain__ server.
To see the list of available Api endpoints, open a web browser and go to `http://<your local IP>:8000/pytrain`.

## Alexa Skill

__PyTrain Api__ is required to use the Alexa __PyTrain__ skill. You may install __PyTrain Api__
using either method described above.

### Configuration

Before getting into the configuration details, lets look at the moving parts involved:

1. Your Lionel Base 3. This device is connected to your local network and receives commands from a Cab 2, Cab 3,
   and/or **PyTrain** server. These commands are relayed onto your layout to control your equipment.

2. Your local network. This can be completely wireless or a hybrid of wired and wireless devices.

3. Your **PyTrain API** server. This is a computer on your local network where you have installed **PyTrain API**.
   It takes requests from the web in a specific format (HTTP or HTTPS), and relays them to your **PyTrain** server.
   **PyTrain API** can run on one of your existing computers (Mac or Windows), or, and a small, inexpensive
   [Raspberry Pi](https://www.raspberrypi.com).

4. Your **PyTrain** server. This is another computer on your local network where you have installed
   [**PyTrain**](https://pypi.org/project/pytrain-ogr/). **PyTrain** receives requests from **PyTrain API**
   and relays them to your Lionel Base 3 in the format it requires. It can also take input from a command
   line (CLI), or be embedded into one or more control panels to operate your layout via physical buttons,
   lights, dials, and knobs. It should run its own [Raspberry Pi](https://www.raspberrypi.com).

5. An [Alexa](https://www.amazon.com/s?k=alexa&crid=7OAPBGIW11Q4&sprefix=alexa%2Caps%2C107&ref=nb_sb_noss_1).
   This device is a wireless speaker and microphone. It supports *skills*, which are voice-activated apps
   that runs on Alexa-enabled devices. You can use Alexa skills to perform tasks like telling jokes,
   getting weather updates, or controlling your Lionel layout. It connects to your local network.

6. Your Gateway. This device connects your local network to *the internet*. It may have been provided by
   your Internet Service Provider (ISP), or you may have purchased it yourself. It is the bridge that lets devices
   on your local network talk to the internet, and lets the internet talk to devices on your local network,
   like your Lionel Base 3.

7. Amazon AWS. Amazon runs several large computing facilities around the world. Alexa skills send the voice
   input from your local alexa's to these computers to interpret, then execute software to fulfill your requests. The
   **PyTrain** Alexa skill runs on these computers, takes yur voice input and then calls back to the **PyTrain API**
   on your local network to control your layout.

Phew! Although there are a lot of moving parts, getting everything up and running is straightforward,
thanks to the **PyTrain** software.

Here is what you need to do:

1. Configure 2 Raspberry Pi 4 or 5 computers. Units with WiFi are preferred, as there are less wires.
2. Install **PyTrain** on one of them and start it with the command:

```aiignore
pytrain -base <ip address of your Lionel Base 3>
```

If you have a Lionel Lcs Ser2, connect it to this computer and start **PyTrain** with this command:

```aiignore
pytrain -ser -base <ip address of your Lionel Base 3>
```

3. Install **PyTrain Api** on the other Raspberry Pi (using one of the two methods above) and start
   it with the appropriate command:

```aiignore
pytrain_api 

or

cli/pytrain_api
```

**PyTrain Api** will automatically find and connect to your **PyTrain** server.

4. Go to your gateway and determine its *external* IP address. This address is assigned by your ISP
   (Comcast, Verizon, Spectrum, etc.). Write it down.
5. Consult your gateway documentation and

### Additional Security

For additional security, you can configure an HTTPS server to proxy requests from the Alexa skill
to the system running __PyTrain API__. This requires:

* Install an additional web server that supports HTTPS secure communication.
  The [nginx](https://nginx.org) web server is an excellent candidate.
* Configure `ngnix` to proxy HTTPS requests from port 443 to port 8000, where they are processed by __PyTrain API__.
* Install an SSL certificate on your dynamic dns service
* Configure the certificate and private key on your local proxy server.

These steps are not required but are suggested if you leave your layout unattended and powered on in order
to prevent unwanted access.