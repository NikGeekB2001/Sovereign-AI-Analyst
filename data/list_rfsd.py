"""List files in RFSD dataset on HuggingFace."""
from huggingface_hub import list_repo_files

files = list_repo_files("irlspbru/RFSD", repo_type="dataset")
print("Files in irlspbru/RFSD:")
for f in files:
    print(f"  {f}")
