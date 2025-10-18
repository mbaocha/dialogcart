# Luma CLI Tools

Command-line interfaces and interactive tools for testing and debugging luma.

## 🎯 Interactive Mode

Test entity extraction interactively with a REPL (Read-Eval-Print Loop).

### Usage

**Option 1: From workspace root**
```bash
cd src
python luma/cli/interactive.py
```

**Option 2: As module**
```bash
cd src
python -m luma.cli.interactive
```

**Option 3: With arguments**
```bash
cd src
python luma/cli/interactive.py --typed  # Use typed format instead of dicts
```

### Features

✅ **Warm Startup**: Preloads models for fast extraction  
✅ **Pretty Output**: Formatted, easy-to-read results  
✅ **Real-time Testing**: Instant feedback on extraction  
✅ **Format Options**: Legacy dict or typed formats  
✅ **Error Handling**: Graceful error messages  

### Example Session

```
============================================================
🎯 Luma Entity Extraction - Interactive Mode
============================================================

Commands:
  - Type a sentence to extract entities
  - Type 'quit' or 'exit' to quit
  - Press Ctrl+C to exit
============================================================

⏳ Initializing pipeline (this may take a few seconds)...
✅ Pipeline ready!


💬 Enter sentence: add 2 kg rice and 3 bags of beans

⚙️  Processing: add 2 kg rice and 3 bags of beans
------------------------------------------------------------

Status:              success
Parameterized:       add 2 kg producttoken and 3 bags of producttoken

Extracted Groups:    2 group(s)

  📦 Group 1:
     Action:      add
     Intent:      add
     Products:    ['rice']
     Brands:      []
     Quantities:  ['2']
     Units:       ['kg']
     Variants:    []

  📦 Group 2:
     Action:      add
     Intent:      add
     Products:    ['beans']
     Brands:      []
     Quantities:  ['3']
     Units:       ['bags']
     Variants:    []

============================================================

💬 Enter sentence: quit

👋 Goodbye!
```

## 📝 Command-Line Arguments

```bash
python luma/cli/interactive.py --help
```

**Options:**
- `--typed`: Use typed `ExtractionResult` format (default: legacy dict)

## 🔧 Development

The interactive CLI is useful for:
- **Manual Testing**: Quick validation of extraction logic
- **Debugging**: Real-time feedback on changes
- **Demonstrations**: Showing extraction capabilities
- **Training**: Teaching how the pipeline works

## 🚀 Future Enhancements

Potential additions:
- [ ] JSON output mode
- [ ] Batch processing from file
- [ ] Comparison mode (semantics vs luma)
- [ ] Performance profiling
- [ ] Export results to file

---

**Source**: Ported from `semantics/entity_extraction_pipeline.py` with enhancements.

