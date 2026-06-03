"""Combine and extract RusLawOD split archive."""
import os
import zipfile

DATA_DIR = os.path.join(os.path.dirname(__file__), "ruslawod")
os.chdir(DATA_DIR)

# Combine split archive: .zip is last part, .z01/.z02/.z03 are first parts
parts = ["corpus_xml_lite.z01", "corpus_xml_lite.z02", "corpus_xml_lite.z03", "corpus_xml_lite.zip"]
combined = "corpus_combined.zip"

print("Combining split archive...")
with open(combined, "wb") as out:
    for p in parts:
        if os.path.exists(p):
            with open(p, "rb") as f:
                out.write(f.read())
            print(f"  Added {p} ({os.path.getsize(p) // 1024 // 1024}MB")

print(f"Combined archive: {os.path.getsize(combined) // 1024 // 1024}MB")

# Patch spanned zip: change "spanned" flag byte at offset 5 from 0x01 to 0x00
print("Patching spanned flag...")
with open(combined, "r+b") as f:
    f.seek(4)
    flag = f.read(1)
    print(f"  Original flag byte: {flag.hex()}")
    f.seek(4)
    f.write(b"\x00")
    print("  Patched to 0x00")

# Extract
print("Extracting...")
try:
    with zipfile.ZipFile(combined, "r") as z:
        z.extractall("corpus_xml_lite")
        file_count = len(z.namelist())
    print(f"Extracted {file_count} files to corpus_xml_lite/")
except Exception as e:
    print(f"ZipFile failed: {e}")
    print("Trying with zipfile.ZipFile(strict=False)...")
    # Alternative: use shutil which delegates to OS
    import shutil
    try:
        shutil.unpack_archive(combined, "corpus_xml_lite")
        print("Extracted via shutil")
    except Exception as e2:
        print(f"shutil also failed: {e2}")
        print("Please install 7-Zip manually and run:")
        print('  7z x corpus_combined.zip -ocorpus_xml_lite -y')

# Show sample
import glob
xml_files = glob.glob("corpus_xml_lite/**/*.xml", recursive=True)
print(f"XML files found: {len(xml_files)}")
if xml_files:
    print(f"Sample: {xml_files[:3]}")
