#!/bin/bash
if [ "$1" = "b" ]; then
    rsync -vr --exclude '.venv' --exclude 'venv' --exclude '.idea' --exclude 'tests/data' --exclude '__pycache__' --exclude 'db' --exclude 'db.sqlite3' ./backend qt:~/ecommerce-django-nextjs/
elif [ "$1" = "f" ]; then
    rsync -vr  --exclude 'node_modules' --exclude '.next' ./frontend qt:~/ecommerce-django-nextjs/
elif [ "$1" = "m" ]; then
    rsync -vr  --exclude 'node_modules' --exclude '.next' ./mainpage qt:~/ecommerce-django-nextjs/
elif [ "$1" = "t" ]; then
    rsync -vr  --exclude '.venv' --exclude '.idea' --exclude '__pycache__' --exclude 'db' --exclude 'db.sqlite3' ./tools qt:~/ecommerce-django-nextjs/
elif [ "$1" = "c" ]; then
    rsync -vr  --exclude '.venv' --exclude '.idea' --exclude '__pycache__' --exclude 'db' --exclude 'db.sqlite3' ./crew qt:~/ecommerce-django-nextjs/
else
    rsync -av ./backend/db.sqlite3 qt:~/ecommerce-django-nextjs/backend/db.sqlite3_
fi
