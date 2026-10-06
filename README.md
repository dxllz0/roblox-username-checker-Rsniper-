# roblox username checker

i made this to check a bunch of roblox names without doing it by hand lol. it just uses the normal validate endpoint and tells you whats free.

it supports proxys, multithreading, resume so you dont check the same names twice, and it can also just generate random names for you.

## how to run

install stuff first:

```
pip install -r requirements.txt
```

needs python 3.10 or newer i think. i use 3.11 on my laptop.

basic usage:

```
python main.py -i usernames.txt -o valid.txt -w 5
```

just generate names instead of using a file:

```
python main.py --generate 1000 --length 4 -o valid.txt
```

with a pattern (? = lowercase, # = number, A = uppercase, * = anything):

```
python main.py --pattern "??##" --generate 5000 -o valid.txt
```

## proxys

proxy.txt gets created automatically the first time you run it. just put one per line like this:

```
127.0.0.1:8080
127.0.0.1:8080:user:pass
http://127.0.0.1:8080
socks5://user:pass@127.0.0.1:1080
```

if you only put ip:port it uses whatever --proxy-type is (http by default).

some flags i actually use:
--proxy-check to test them first, --proxy-mode random sometimes works better, --no-proxy-fallback if you dont want it to use your own ip.

## other useful stuff

-i input file, -o where the free names go. -w is how many threads, i usually use 5-10.

--resume skips names already in checked.txt so you can just ctrl+c and continue later.

--results-file results.jsonl logs everything with codes and ms, useful if you wanna filter later.

--birthday / --random-birthday because roblox wants a birthday in the request. any date works.

thats basically it. its unofficial so dont spam it too hard or roblox will rate limit you for a bit.
