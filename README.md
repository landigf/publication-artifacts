# WebTrace anonymous reviewer artifact

[Download the artifact ZIP](https://anonymous.4open.science/api/repo/WebTrace-WebConf2027-753/file/WebTrace-WebConf2027-anonymous-artifact.zip?download=true&v=7ac485caa668de737b79bd9a588f31dadbf926264ebafe40866189342b48a4cf).

The ZIP contains the fixed inputs, replay code, reports, figures, and pinned
dependency list used by the accompanying anonymous manuscript. It preserves the
`WebTrace/` directory and all internal file paths.

Verify the archive before extraction:

```sh
printf '%s  %s\n' '7ac485caa668de737b79bd9a588f31dadbf926264ebafe40866189342b48a4cf' 'WebTrace-WebConf2027-anonymous-artifact.zip' | shasum -a 256 -c -
unzip WebTrace-WebConf2027-anonymous-artifact.zip
cd WebTrace
```

Use Python 3.12 and the pinned dependency list, then run the reproduction command:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
BROWSETRACE_PYTHON=.venv/bin/python sh REPRODUCE.sh
```

Dependency installation requires package downloads. Once dependencies are
installed, reproduction reads local files and needs no model or provider access.
The verifier checks the file inventory and reruns all 60 cache replays. The
archive's `README.md` describes the evaluated workload, privacy transformations,
and interpretation limits. The collection implementation is included for
inspection and is not executed by `REPRODUCE.sh`.

Code: Apache-2.0. Data: CC BY 4.0. Full license texts are included in the archive.
