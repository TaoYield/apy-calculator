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
# For subnet validator APY calculation (netuid > 0)
docker run -t tao-yield-calculator 37 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp 24h

# For root network validator APY calculation (netuid = 0)
docker run -t tao-yield-calculator 0 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp 24h
```

You can pass additional environment variables to the container to customize the script's behavior:

| Variable | Description | Default |
|----------|-------------|---------|
| NODE | The archive node to use to fetch the data from. | Opentensor Foundation Archive Node |
| BATCH_SIZE | The batch size of tasks to run asynchronously. Be careful when using docker. | 100 |
| INHERITED | The inherited flag defines if inherited have to be used. It needs more data to be retrieved. | False |
| NO_FILTERS | The flag defines if filters will be applied to validators. | False |

Example with custom parameters:

```bash
docker run -t -e NODE="wss://archive.chain.opentensor.ai:443" tao-yield-calculator 37 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp 24h
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
# For subnet validator APY calculation (netuid > 0)
python src/main.py 37 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp 24h

# For root network validator APY calculation (netuid = 0)
python src/main.py 0 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp 24h
```

You can set environment variables to customize the script's behavior, e.g.:

```bash
NODE="wss://archive.chain.opentensor.ai:443" python src/main.py 37 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp 24h
```

## Command Line Interface

The tool accepts the following command-line arguments:

```bash
python src/main.py <netuid> <hotkey> <interval> [block]

Arguments:
  <netuid>   - netuid index (0 is root network, >0 for subnet)
  <hotkey>   - validator hotkey in ss58 format
  <interval> - one of: "1h", "24h", "7d", "30d"
  [block]    - optional block number to calculate APY from
```

Example:

```bash
python src/main.py 37 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp 24h
```

## Implementation Details

The calculator uses the following approach for validator APY calculations:

1. **Subnet validators (netuid > 0)** — `src/subnet_calc.py`:
   - For each epoch boundary in the window, reads `AlphaDividendsPerSubnet[netuid, hotkey]`
     — the alpha the hotkey earned on that subnet's coinbase.
   - Since subtensor v3.3.0 (spec 361, ~Dec 2025), `AlphaDividendsPerSubnet` no longer
     includes the root-side component (that moved to `RootAlphaDividendsPerSubnet`), so
     no deduction is applied — the value read is used directly. See PR #6 for context.
   - Yields are compounded per-epoch and annualized over the requested interval.

2. **Root network (netuid = 0)** — `src/root_calc.py`:
   - The runtime source of root dividends changed at spec 441 ("Root Reborn",
     ~2026-07-27). The calculator detects the boundary from runtime metadata
     (`SubtensorModule.BasketDepositedTao` presence) and picks its source accordingly:
   - **Pre-441 blocks**: legacy path — reads `RootClaimable[hotkey]` (per-netuid α/TAO
     claimable rate) at each epoch boundary, computes Δα/TAO, converts to TAO via the
     subnet's AMM price at that block, compounds.
   - **Post-441 blocks**: basket path — samples `BasketDepositedTao[hotkey]` (cumulative
     TAO deposited to the validator's escrowed basket) across the window. Δ deposited
     over Δ time / root stake = period yield, then annualized. Reads the whole delta
     over the observed span rather than the nominal window, so partial coverage during
     the rollout does not silently understate APY.
   - Both paths use `TotalHotkeyAlpha[hotkey, netuid=0]` for the root-stake denominator.

3. **Snapshot mode** (experimental, `snapshot-calc` branch) — `src/root_calc_epoch.py`:
   - Alternative post-441 root APY that reads `RootAlphaDividendsPerSubnet[netuid, hotkey]`
     directly (subtensor's per-epoch atomic write, unaffected by the round-robin basket
     flush that jitters the basket path on short windows).
   - Walks `LastEpochBlock` backward to reconstruct exact plateau boundaries, integrates
     `plateau_alpha × price × plateau_blocks / tempo` per subnet, compounds. Same 1h APY
     the basket path gives but without the ±1.5pp round-robin jitter — settles to ±0.05pp
     between adjacent windows on identical underlying rate.
   - Not on `master` yet; useful reference implementation for anyone porting the
     jitter-free approach elsewhere.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
