import ast
content = open('telegram_bot.py', encoding='utf-8').read()
lines = content.split('\n')

new_code = open('_new_helpers.py', encoding='utf-8').read()
insert_lines = new_code.split('\n')
new_lines = lines[:18764] + insert_lines + lines[18764:]
new_content = '\n'.join(new_lines)

try:
    ast.parse(new_content)
    print('SYNTAX OK')
    open('telegram_bot.py', 'w', encoding='utf-8').write(new_content)
    print('Total lines:', len(new_lines))
except SyntaxError as e:
    print('ERROR:', e)
