import re
import glob

replacements = {
    r'\bbg-slate-900\b': 'bg-slate-50 dark:bg-slate-900',
    r'\bbg-slate-800\b': 'bg-white dark:bg-slate-800',
    r'\bbg-slate-800/50\b': 'bg-white/50 dark:bg-slate-800/50',
    r'\bborder-slate-800\b': 'border-slate-200 dark:border-slate-800',
    r'\bborder-slate-700\b': 'border-slate-300 dark:border-slate-700',
    r'\btext-slate-100\b': 'text-slate-900 dark:text-slate-100',
    r'\btext-slate-200\b': 'text-slate-800 dark:text-slate-200',
    r'\btext-slate-300\b': 'text-slate-700 dark:text-slate-300',
    r'\btext-slate-400\b': 'text-slate-600 dark:text-slate-400',
    r'\btext-white\b': 'text-slate-900 dark:text-white',
    r'\bhover:bg-slate-800\b': 'hover:bg-slate-200 dark:hover:bg-slate-800',
    r'\bhover:bg-slate-700\b': 'hover:bg-slate-300 dark:hover:bg-slate-700',
    r'\bhover:bg-slate-800/50\b': 'hover:bg-slate-100/50 dark:hover:bg-slate-800/50',
    r'\bhover:text-white\b': 'hover:text-slate-900 dark:hover:text-white',
    r'\bfocus:border-slate-700\b': 'focus:border-slate-300 dark:focus:border-slate-700',
    r'\bdivide-slate-800\b': 'divide-slate-200 dark:divide-slate-800',
}

files = glob.glob('src/web/templates/*.html')
for file in files:
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Pre-clean: Remove any previously applied `dark:` replacements accidentally running twice
    # This script assumes files have not been modified yet.

    for pattern, replacement in replacements.items():
        content = re.sub(pattern, replacement, content)

    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)

print("Done replacing classes.")
