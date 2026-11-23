# Virtual Diode Stand Research Artifact

Code and experimental evidence accompanying *Verifying Minimum-Version Compliance in Unidirectional Telemetry under Incomplete Observations*.

[Open release v1.0.0](https://github.com/aizexss/virtual-diode-stand/releases/tag/v1.0.0) · [Download the complete verification bundle](https://github.com/aizexss/virtual-diode-stand/releases/download/v1.0.0/Virtual_Diode_Stand_Reproduction_v1.0.0.zip)

## Verify the saved experiments

Download the release bundle and extract it to a short writable path, for example `C:\vd` on Windows. From that directory, run:

```powershell
py -3.12 -B .\verify_release.py
```

The command checks the archive hashes, verifies the saved network and application records, and compares the regenerated outputs with the frozen results. Each stage prints a result and the command exits nonzero on failure. It uses CPython 3.12 and the Python standard library. The separate instructions in the addendum specify the QEMU and Ubuntu versions for fresh VM runs.

| Locate | What you can check |
| --- | --- |
| [Method source](method/) | The three frozen Python modules for profile validation, explanation generation, and explanation checking. |
| `DataDiode_Research_Testbed.zip` | Full method, finite reference corpus, signed software controls, and pinned dependencies. |
| `Reproduction_Addendum.zip` | Input records, test protocol, PCAPs for replay at the floor and below the floor, measured-only and missing-read cases, and comparison and threshold controls. See `Reproduction_Addendum/README.md`. |
| `GoDiode_F04_Offline_Evidence.zip` | Public evidence for checking the archived profile and explanation in the retrospective GoDiode case. The packet-authentication stage and its lab credentials are outside the public subset. |

For the below-floor replay, open `Reproduction_Addendum/replay_below_floor/README.md`. The saved case uses a floor of 5 and shows a repeat of version 4 arriving at the receiver. Publication and the application read remain at version 6.

The software results concern the instrumented testbeds and the declared evidence models. The network trials use virtual machines. For upstream component terms, see the license notices included with the respective files.
