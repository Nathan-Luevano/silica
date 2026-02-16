# silica

Exhaustive differential validation of the AArch64 instruction decode space.
Every one of the 4,294,967,296 possible A64 encodings, decoded through
multiple disassemblers, diffed against ARM's own machine-readable
architecture specification.

See DESIGN-FINAL.md for the full design and build manual. This project is
in progress: `GOALS.yml` and `silica verify` are the only source of truth
on what actually works.

## Running

```bash
micromamba create -y -p ./.venv -f environment.yml
micromamba run -p ./.venv silica doctor
make check
```
