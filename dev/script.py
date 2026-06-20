import re

with open('fidal_cds_tool.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_func = re.search(r'(def _opt_assign_best\(.*?)def _compute_optimal_py', text, re.DOTALL)
if old_func:
    with open('old_opt.txt', 'w', encoding='utf-8') as f:
        f.write(old_func.group(1))
    print('OK')
