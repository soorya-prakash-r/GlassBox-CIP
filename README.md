# GlassBox-CIP – Third Commit

## Project Overview

This commit introduces the main parser extraction logic and supporting files for analyzing and normalizing code structures.

The system uses **Tree-sitter** to extract and process syntax trees for C programs, generating normalized abstract representations for structural analysis and validation.

```bash

## File Structure

GlassBox-CIP/
│
├── src/
│   ├── test.py                  # Main code for extracting normalized parser from Tree-sitter
│   └── test_c/                  # Folder containing sample C test files
│
├── format-testing/
│   └── GlassBox-CIP/
│       └── structure.txt        # Structure reference file for testing/formatting
│
└── README.md                    # Project documentation

```

## Key Files

src/test.py
- Implements the main logic for parsing and normalizing syntax trees
- Uses Tree-sitter to generate abstract representations of input C programs

format-testing/GlassBox-CIP/structure.txt
- Defines the expected structure or format reference for parsed code

src/test_c/
- Contains sample C files
- Used to test the parser and validate normalized outputs

---

## Usage

1. Place the C program(s) you want to parse into:
   src/test_c/

2. Run the main parser:
   python src/test.py

3. Compare the generated output with:
   format-testing/GlassBox-CIP/structure.txt

---

## Purpose of This Commit

- Introduces core parsing logic
- Establishes normalization workflow
- Provides structural validation reference
- Enables testing using sample C files
