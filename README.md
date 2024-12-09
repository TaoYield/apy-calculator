# TaoYield APY Calculator

This repository contains a Python script that calculates the APY and effective take rate for a given Bittensor validator. The implementation includes access to the on-chain data, mathematics, and logic to make the calculations, exactly the same as we use for [TaoYield](https://taoyield.com).

For a more detailed explanation of the mathematics, please refer to our [documentation](https://taoyield.com/docs).

After running the script, you can compare the results with the [TaoYield](https://taoyield.com) dashboard. If you find any discrepancies, re-run the script or wait 15-30 minutes as there's a chance the validator's data is still being updated on the dashboard. For 30d period, especially when using the Opentensor Foundation archive node, the calculation time may be significant to the point where the validator's data on the dashboard might get updated during the calculation, potentially leading to differences between the script's output and the displayed APY.

## Running the script with Docker (recommended)

### Prerequisites

- Docker installed on your system

### Steps

1. Build the Docker image:
```bash
docker build -t tao-yield-calculator .
```

2. Run the Docker container:
```bash
docker run -t tao-yield-calculator
```

You can pass additional environment variables to the container to customize the script's behavior:

| Variable | Description | Default |
|----------|-------------|---------|
| PERIOD   | The period to calculate the APY for. Options: `1h`, `24h`, `7d`, or `30d`. Longer periods take more time to calculate. Estimated durations: `1h` ~10s, `24h` ~1min, `7d` ~10min, `30d` ~45min. Using your own archive node will significantly speed up the process. | `24h` |
| HOTKEY   | The hotkey of the validator to calculate the APY for. | Owl Ventures Hotkey |
| NODE     | The archive node to use to fetch the data from. | Opentensor Foundation Archive Node |

Example with custom parameters:
```bash
docker run -e PERIOD="24h" -e HOTKEY="5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp" -e NODE="wss://archive.chain.opentensor.ai:443" -t tao-yield-calculator
```

## Running the script without Docker

### Prerequisites

- Python 3.10 or higher installed on your system.

### Steps

1. Create a virtual environment:
```bash
python -m venv venv
```

2. Activate the virtual environment:
```bash
source venv/bin/activate
```

3. Install the required packages:
```bash
pip install -r requirements.txt
```

4. Run the script:
```bash
python src/main.py
```

You can set the same environment variables as in the Docker to customize the script's behavior, e.g.:
```bash
PERIOD="24h" HOTKEY="5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp" NODE="wss://archive.chain.opentensor.ai:443" python src/main.py
```
