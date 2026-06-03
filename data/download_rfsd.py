"""Download RFSD dataset from HuggingFace (parquet files by year)."""
import os
from huggingface_hub import hf_hub_download

DATA_DIR = os.path.join(os.path.dirname(__file__), "rfsd_dataset")
os.makedirs(DATA_DIR, exist_ok=True)

years = range(2011, 2025)
for year in years:
    path = f"RFSD/year={year}/part-0.parquet"
    local = os.path.join(DATA_DIR, f"year_{year}.parquet")
    if os.path.exists(local):
        size_mb = os.path.getsize(local) / 1024 / 1024
        print(f"  {year}: already exists ({size_mb:.1f}MB)")
        continue
    print(f"  Downloading {year}...")
    try:
        downloaded = hf_hub_download(
            repo_id="irlspbru/RFSD",
            filename=path,
            repo_type="dataset",
        )
        import shutil
        shutil.copy2(downloaded, local)
        size_mb = os.path.getsize(local) / 1024 / 1024
        print(f"  {year}: done ({size_mb:.1f}MB)")
    except Exception as e:
        print(f"  {year}: ERROR - {e}")

# Summary
import glob
files = glob.glob(os.path.join(DATA_DIR, "*.parquet"))
total_size = sum(os.path.getsize(f) for f in files) / 1024 / 1024
print(f"\nTotal: {len(files)} files, {total_size:.1f}MB")
