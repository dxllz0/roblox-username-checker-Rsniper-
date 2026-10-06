# RSniper - Roblox username sniper
import argparse  # CLI argument parsing
import concurrent.futures
import csv  # kept for CSV result output
import datetime
import json
import random
import re
import string
import threading
import time

import requests
from colorama import Fore, init

init(autoreset=True)

# roblox usernames: 3-20 chars, alphanumeric + underscore
USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,20}$')
DEFAULT_CHARSET = string.ascii_letters + string.digits + '_'

_tls = threading.local()
_print_lock = threading.Lock()
_proxy_lock = threading.Lock()
_sessions_lock = threading.Lock()
_circuit_lock = threading.Lock()

_proxies = []  # active proxy URLs
_proxy_idx = 0
_proxy_fails: dict = {}
_all_sessions = []
_circuit_until = 0.0


def log(msg, color=Fore.WHITE):
    with _print_lock:
        print(color + msg)


# proxy handling :)

DEFAULT_PROXY_FILE = 'proxy.txt'
PROXY_TEMPLATE = """# Add one proxy per line.
# Supported formats:
#   ip:port
#   ip:port:user:pass
#   http://ip:port  https://ip:port  socks4://ip:port  socks5://ip:port
#   socks5://user:pass@ip:port
# Bare ip:port entries use --proxy-type (default http).
# Example:
# 127.0.0.1:8080
"""

def ensure_proxy_file(path):
    import os
    if os.path.exists(path):
        return False
    try:
        f = open(path, 'w', encoding='utf-8')
        f.write(PROXY_TEMPLATE)
        f.close()
        log('Created ' + path + ', add proxies there.', Fore.CYAN)
        return True
    except Exception as e:
        print(Fore.RED + f"Could not create {path}: {e}")
        return False


def _port_ok(port, host):
    # shared validation for host/port pairs
    if not port.isdigit():
        return False
    try:
        p = int(port)
    except:
        return False
    if p < 1 or p > 65535:
        return False
    if host == '' or host is None:
        return False
    return True


def _parse_with_scheme(line):
    for scheme in ('socks5://', 'socks4://', 'https://', 'http://'):
        if not line.startswith(scheme):
            continue
        name = scheme[:-3]
        rest = line[len(scheme):]
        if '@' in rest:
            auth, hostport = rest.rsplit('@', 1)
            if ':' not in hostport or ':' not in auth:
                return None
            tmp = hostport.rsplit(':', 1)
            host, port = tmp[0], tmp[1]
            if not _port_ok(port, host):
                return None
            return name + '://' + auth + '@' + host + ':' + port
        if ':' not in rest:
            return None
        tmp = rest.rsplit(':', 1)
        host, port = tmp[0], tmp[1]
        if not _port_ok(port, host):
            return None
        return "%s://%s:%s" % (name, host, port)
    return 'NO_SCHEME'


def parse_proxy_line(line, default_type='http'):
    line = line.strip()
    if line == '' or line.startswith('#'):
        return None
    # entries with an explicit scheme
    got = _parse_with_scheme(line)
    if got != 'NO_SCHEME':
        return got
    parts = line.split(':')
    if len(parts) == 2:
        host, port = parts
        if not _port_ok(port, host):
            return None
        return default_type + '://' + host + ':' + port
    if len(parts) == 4:
        # ip:port:user:pass
        host, port, user, pwd = parts
        if not _port_ok(port, host):
            return None
        if not user:
            return None
        return f"{default_type}://{user}:{pwd}@{host}:{port}"
    return None  # support user:pass@ip:port form


def load_proxies(path, default_type="http"):
    global _proxy_idx
    proxies = []
    seen = set()
    try:
        f = open(path, "r", encoding="utf-8")
        lines = f.read().splitlines()
        f.close()
    except FileNotFoundError:
        log(f"No {path} found, continuing without proxies.", Fore.YELLOW)
        with _proxy_lock:
            _proxy_idx = 0
            _proxy_fails.clear()
        return []
    except OSError as e:
        log('Cannot read proxy file: ' + str(e), Fore.RED)
        return []

    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if s == '' or s.startswith('#'):
            continue
        url = parse_proxy_line(raw, default_type)
        if url is None:
            log("Line " + str(i) + " invalid: " + s, Fore.YELLOW)
            continue
        if url not in seen:
            seen.add(url)
            proxies.append(url)
    with _proxy_lock:
        _proxy_idx = 0
        _proxy_fails.clear()
    log(f"Loaded {len(proxies)} proxies from {path}.", Fore.CYAN)
    return proxies


def get_next_proxy(mode):
    global _proxy_idx
    with _proxy_lock:
        if len(_proxies) == 0:
            return None
        if mode == 'random':
            return random.choice(_proxies)
        p = _proxies[_proxy_idx % len(_proxies)]
        _proxy_idx += 1
        return p


def note_proxy_fail(proxy, max_fails):
    if proxy is None:
        return False
    with _proxy_lock:
        c = _proxy_fails.get(proxy, 0) + 1
        _proxy_fails[proxy] = c
        if c >= max_fails and proxy in _proxies:
            _proxies.remove(proxy)
            log(f"Proxy {proxy} removed after {max_fails} failures ({len(_proxies)} left).", Fore.RED)
            return True
    return False


def get_session_for_proxy(proxy):
    if not hasattr(_tls, 'sessions'):
        _tls.sessions = {}
    if proxy not in _tls.sessions:
        s = requests.Session()
        s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        s.trust_env = False
        if proxy:
            s.proxies = {'http': proxy, 'https': proxy}
        _tls.sessions[proxy] = s
        with _sessions_lock:
            _all_sessions.append(s)
    return _tls.sessions[proxy]


def close_all_sessions():
    with _sessions_lock:
        sessions = list(_all_sessions)
        _all_sessions.clear()
    for s in sessions:
        try:
            s.close()
        except:
            pass
    try:
        _tls.sessions = {}
    except AttributeError:
        pass


# rate limit

def note_429(wait):
    global _circuit_until
    with _circuit_lock:
        if time.time() + wait > _circuit_until:
            _circuit_until = time.time() + wait


def wait_for_circuit():
    with _circuit_lock:
        rem = _circuit_until - time.time()
    if rem > 0:
        if rem > 30:
            rem = 30
        time.sleep(rem)



def random_birthday(year_start, year_end):
    y = random.randint(year_start, year_end)
    m = random.randint(1, 12)
    d = random.randint(1, 28)  # cap ts at 28 to avoid invalid dates
    return "%04d-%02d-%02d" % (y, m, d)


def birthday_type(s):
    try:
        datetime.datetime.strptime(s, '%Y-%m-%d')
    except:
        raise argparse.ArgumentTypeError('Birthday must be YYYY-MM-DD.')
    return s


def parse_birthday_range(s):
    try:
        a_str, b_str = s.split(':')
        a = int(a_str)
        b = int(b_str)
    except:
        raise argparse.ArgumentTypeError('Use format 1990:2005.')
    if a > b:
        raise argparse.ArgumentTypeError('Range start must be <= end.')
    if not 1900 <= a <= 2026 or not 1900 <= b <= 2026:
        raise argparse.ArgumentTypeError('Years must be 1900-2026.')
    return (a, b)


def workers_type(s):
    v = int(s)
    if v < 1:
        raise argparse.ArgumentTypeError('Workers must be >= 1.')
    return v

def sleep_type(s):
    v = float(s)
    if v < 0:
        raise argparse.ArgumentTypeError("Sleep must be >= 0.")
    return v

def generate_count_type(s):
    v = int(s)
    if v < 0:
        raise argparse.ArgumentTypeError('Generate count must be >= 0.')
    return v

def name_len_type(s):
    v = int(s)
    if v < 3 or v > 20:
        raise argparse.ArgumentTypeError('Length must be 3-20 per Roblox rules.')
    return v


def resolve_birthday(args):
    if args.random_birthday:
        return random_birthday(args.birthday_range[0], args.birthday_range[1])
    return args.birthday


def render_pattern(pattern, charset):
    out = ''
    for ch in pattern:
        if ch == '?':
            out += random.choice(string.ascii_lowercase)
        elif ch == '#' or ch == '1':
            out += random.choice(string.digits)
        elif ch == 'A':
            out += random.choice(string.ascii_uppercase)
        elif ch == '*':
            out += random.choice(charset)
        else:
            out += ch
    return out


def iter_generated(n, length, charset, min_len, max_len, pattern):
    for _ in range(n):
        if pattern:
            yield render_pattern(pattern, charset)
        else:
            if min_len and max_len:
                ln = random.randint(min_len, max_len)
            else:
                ln = length
            name = ''.join(random.choice(charset) for _ in range(ln))
            yield name


def iter_file_names(path):
    f = open(path, 'r', encoding='utf-8')
    for line in f:
        yield line.rstrip('\n')
    f.close()



def _retry_wait(resp, attempt):
    # prefer retry-after header
    try:
        w = float(resp.headers.get('Retry-After', 2 ** attempt))
    except:
        w = float(2 ** attempt)
    return w


def _show_result(code, username, message, extra, quiet):
    # centralized result logging extracted to keep check_username flat!!
    if quiet:
        if code == 0:
            log('Available: ' + username + extra, Fore.GREEN)
        return
    if code == 0:
        log('Available: ' + username + extra, Fore.GREEN)
        return
    if code == 1:
        log("Taken: " + username + extra, Fore.LIGHTBLACK_EX)
        return
    if code == 2:
        log(f"Censored: {username}{extra}", Fore.RED)
        return
    log('Unknown (' + str(code) + '): ' + username + ' ' + message + extra, Fore.YELLOW)


def _single_attempt(username, bday, sess, proxy, attempt, quiet, verbose):
    # status: 'ok', 'retry', 'fail'
    address = 'https://auth.roblox.com/v1/usernames/validate'
    t0 = time.time()
    r = sess.get(address, params={'request.username': username, 'request.birthday': bday}, timeout=10)
    ms = int((time.time() - t0) * 1000)
    if r.status_code == 429:
        w = _retry_wait(r, attempt)
        note_429(w)
        if not quiet:
            log(f"Rate limited on {username}, waiting {w}s.", Fore.YELLOW)
        time.sleep(w)
        return 'retry', None, '', ms
    if 500 <= r.status_code < 600:
        if not quiet:
            log('Server error ' + str(r.status_code) + ' for ' + username + ', attempt ' + str(attempt + 1), Fore.YELLOW)
        time.sleep(2 ** attempt)
        return 'retry', None, '', ms
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        if not quiet:
            log('Invalid JSON for ' + username + ': ' + r.text[:200], Fore.YELLOW)
        return 'fail', None, 'bad json', ms
    code = data.get('code')
    message = data.get('message', '')
    if verbose:
        extra = ' ' + str(ms) + 'ms via ' + (proxy if proxy else 'direct')
    else:
        extra = ''
    _show_result(code, username, message, extra, quiet)
    return 'ok', code, message, ms


def check_username(username, args, max_retries=3):
    last_msg = ''
    proxy = None
    for attempt in range(max_retries + 1):
        wait_for_circuit()
        if len(_proxies) > 0:
            proxy = get_next_proxy(args.proxy_mode)
        else:
            proxy = None
        if _proxies and proxy is None and args.no_proxy_fallback:
            return None, 'No proxies remaining.', 0
        bday = resolve_birthday(args)
        sess = get_session_for_proxy(proxy)
        t0 = time.time()
        try:
            status, code, msg, ms = _single_attempt(username, bday, sess, proxy, attempt, args.quiet, args.verbose)
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            ms = int((time.time() - t0) * 1000)
            last_msg = str(e)[:200]
            if proxy:
                note_proxy_fail(proxy, args.max_proxy_fails)
            if attempt >= max_retries:
                log('Connection failed for ' + username + ': ' + str(e)[:120], Fore.YELLOW)
                return None, last_msg, ms
            if not args.quiet:
                log('Proxy ' + str(proxy) + ' failed for ' + username + ': ' + str(e)[:100], Fore.YELLOW)
            time.sleep(1)
            continue
        except Exception as e:
            # remaining errors retry with backoff
            ms = int((time.time() - t0) * 1000)
            last_msg = str(e)[:200]
            if attempt >= max_retries:
                log('Error for ' + username + ': ' + str(e), Fore.YELLOW)
                return None, last_msg, ms
            if not args.quiet:
                log("Error for %s: %s, retry %d" % (username, e, attempt + 1), Fore.YELLOW)
            time.sleep(2 ** attempt)
            continue
        if status == 'retry':
            continue
        if status == 'fail':
            return None, msg, ms
        last_msg = msg
        return code, msg, ms
    return None, last_msg, 0


def write_result(handle, lock, fmt, record):
    if handle is None:
        return
    with lock:
        if fmt == 'csv':
            # manual CSV formatting to avoid extra dependency handling
            handle.write(record['username'] + ',' + str(record['code']) + ',' + str(record['latency_ms']) + ',"' + record['message'].replace('"', '') + '"\n')
        else:
            handle.write(json.dumps(record) + '\n')
        handle.flush()


def check_and_save(username, args, file_lock, out_handle, results_handle, results_lock, checked_handle, stats):
    code, msg, lat = check_username(username, args)
    with stats['lock']:
        if code == 0:
            stats['valid'] = stats['valid'] + 1
        elif code == 1:
            stats['taken'] += 1
        elif code == 2:
            stats['censored'] += 1
        elif code is None:
            stats['error'] += 1
        else:
            stats['unknown'] += 1
    if code == 0 and out_handle is not None:
        with file_lock:
            if args.out_format == 'jsonl':
                out_handle.write(json.dumps({'username': username}) + '\n')
            elif args.out_format == 'csv':
                out_handle.write(username + '\n')
            else:
                out_handle.write(username + "\n")
            out_handle.flush()
    if results_handle is not None:
        write_result(results_handle, results_lock, args.results_format, {'username': username, 'code': code, 'message': msg, 'latency_ms': lat, 'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    if checked_handle is not None:
        with file_lock:
            checked_handle.write(username + '\n')
    time.sleep(args.sleep)
    return username, code


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Roblox username availability checker.')
    p.add_argument('-i', '--input', default='usernames.txt', help='Input file with usernames.')
    p.add_argument('-o', '--output', default='valid.txt', help='Output file for available names.')
    p.add_argument('--out-format', choices=['txt', 'jsonl', 'csv'], default='txt')
    p.add_argument('--results-file', default=None, help='Detailed log file.')
    p.add_argument('--results-format', choices=['jsonl', 'csv'], default='jsonl')
    p.add_argument('--checked-file', default='checked.txt', help='Tracks already checked names.')
    p.add_argument('--resume', action='store_true', help='Skip already checked names.')
    p.add_argument('--no-cache', action='store_true')
    p.add_argument('-w', '--workers', type=workers_type, default=5, help='Concurrent worker count.')
    p.add_argument('--sleep', type=sleep_type, default=0.2)
    p.add_argument('--birthday', type=birthday_type, default='2000-01-01')
    p.add_argument('--random-birthday', action='store_true', help='Use a random birthday per request.')
    p.add_argument('--birthday-range', type=parse_birthday_range, default=(1990, 2005))
    p.add_argument('--proxy-file', default=DEFAULT_PROXY_FILE)
    p.add_argument('--proxy-type', choices=['http', 'https', 'socks4', 'socks5'], default='http')
    p.add_argument('--proxy-mode', choices=['round-robin', 'random'], default='round-robin')
    p.add_argument('--max-proxy-fails', type=int, default=3)
    p.add_argument('--no-proxy-fallback', action='store_true')
    p.add_argument('--proxy-check', action='store_true', help='Health-check proxies before starting.')
    p.add_argument('--generate', type=generate_count_type, default=0, help='Number of random names to generate.')
    p.add_argument('--length', type=name_len_type, default=4)
    p.add_argument('--min-len', type=name_len_type, default=None)
    p.add_argument('--max-len', type=name_len_type, default=None)
    p.add_argument('--charset', default=DEFAULT_CHARSET)
    p.add_argument('--pattern', default=None, help="? lower, #/1 digit, A upper, * any, e.g. ??##")
    p.add_argument('-q', '--quiet', action='store_true', help='Only show available names.')
    p.add_argument('-v', '--verbose', action='store_true')
    return p.parse_args(argv)


def load_checked(path, enabled):
    if not enabled:
        return set()
    try:
        f = open(path, 'r', encoding='utf-8')
        out = set()
        for l in f:
            l = l.strip()
            if l:
                out.add(l)
        f.close()
        return out
    except FileNotFoundError:
        return set()
    except OSError:
        return set()


def proxy_health_check(args):
    if len(_proxies) == 0:
        return
    count = len(_proxies)
    log('Health-checking ' + str(count) + ' proxies...', Fore.CYAN)
    dead = []
    for proxy in list(_proxies):
        s = requests.Session()
        s.trust_env = False
        s.proxies = {'http': proxy, 'https': proxy}
        s.headers.update({'User-Agent': 'Mozilla/5.0'})
        try:
            rr = s.get('https://auth.roblox.com/v1/usernames/validate', params={'request.username': 'healthcheckprobe123', 'request.birthday': '2000-01-01'}, timeout=8)
        except Exception:
            try:
                s.close()
            except:
                pass
            dead.append(proxy)
            continue
        if rr.status_code == 200 or rr.status_code == 429:
            s.close()
            continue
        s.close()
        dead.append(proxy)
    with _proxy_lock:
        for failed in dead:
            if failed in _proxies:
                _proxies.remove(failed)
    log("Proxies: " + str(len(_proxies)) + " alive, " + str(len(dead)) + " removed.", Fore.CYAN)


def _prepare_name(raw, seen, quiet):
    # normalize and filter a name returns none to skip
    name = raw.strip()
    if name == '':
        return None
    if name in seen:
        return None
    seen.add(name)
    if not USERNAME_RE.match(name):
        if not quiet:
            log('Skipping invalid name: ' + name, Fore.YELLOW)
        return None
    return name


def _collect_jobs(ex, stream, seen, args, file_lock, out_handle, results_handle, results_lock, checked_handle, stats):
    futs = {}
    total = 0
    for raw in stream():
        name = _prepare_name(raw, seen, args.quiet)
        if name is None:
            continue
        job = ex.submit(check_and_save, name, args, file_lock, out_handle, results_handle, results_lock, checked_handle, stats)
        futs[job] = name
        total += 1
    return futs, total


def _wait_done(futs, total, stats):
    done = 0
    if total == 0:
        log('Nothing to check.', Fore.YELLOW)
        return
    for fut in concurrent.futures.as_completed(futs):
        done += 1
        try:
            fut.result()
        except Exception as e:
            log('Error for ' + futs[fut] + ': ' + str(e), Fore.RED)
        if done % 50 != 0 and done != total:
            continue
        with stats['lock']:
            snap = dict(stats)
            del snap['lock']
        log(str(done) + '/' + str(total) + ' ' + str(snap), Fore.CYAN)


def _run(args, seen, stats, file_lock, results_lock, out_handle, results_handle, checked_handle, stream):
    total = 0
    try:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
        with ex:
            futs, total = _collect_jobs(ex, stream, seen, args, file_lock, out_handle, results_handle, results_lock, checked_handle, stats)
            _wait_done(futs, total, stats)
    except KeyboardInterrupt:
        log('Interrupted (Ctrl+C).', Fore.RED)
        return {'total': total, 'ret': 130}
    if total == 0:
        return {'total': 0, 'ret': 0}
    return {'total': total, 'ret': 0}


def main(argv=None):
    global _proxies
    args = parse_args(argv)

    if args.charset == '':
        log('Charset must not be empty.', Fore.RED)
        return 2
    if (args.min_len is None) != (args.max_len is None):
        log('Use --min-len and --max-len together.', Fore.RED)
        return 2
    if args.min_len is not None and args.min_len > args.max_len:
        log('Min length must be <= max length.', Fore.RED)
        return 2
    if args.pattern and len(args.pattern) > 20:
        print(Fore.RED + 'Pattern max length is 20.')
        return 2
    if args.max_proxy_fails < 1:
        log("Max proxy fails must be >= 1.", Fore.RED)
        return 2

    ensure_proxy_file(DEFAULT_PROXY_FILE)
    if args.proxy_file and args.proxy_file != DEFAULT_PROXY_FILE:
        ensure_proxy_file(args.proxy_file)

    if args.proxy_file:
        _proxies = load_proxies(args.proxy_file, args.proxy_type)
    else:
        with _proxy_lock:
            global _proxy_idx
            _proxy_idx = 0
            _proxy_fails.clear()
        _proxies = []

    if args.proxy_check:
        proxy_health_check(args)
        if _proxies:
            with _proxy_lock:
                _proxy_idx = 0
        elif args.no_proxy_fallback and args.proxy_file:
            log('All proxies dead and fallback disabled.', Fore.RED)
            return 1

    checked = load_checked(args.checked_file, args.resume)
    if len(checked) > 0:
        log('Skipping ' + str(len(checked)) + ' already checked names.', Fore.CYAN)

    # stream names to keep memory usage flat for large inputs
    def _file_part():
        try:
            reader = iter_file_names(args.input)
        except FileNotFoundError:
            # missing input is reported below
            return
            yield
        for line in reader:
            yield line

    def name_stream():
        yield from _file_part()
        if args.generate > 0:
            yield from iter_generated(args.generate, args.length, args.charset, args.min_len, args.max_len, args.pattern)

    seen = set(checked)
    file_lock = threading.Lock()
    results_lock = threading.Lock()
    stats = {'valid': 0, 'taken': 0, 'censored': 0, 'unknown': 0, 'error': 0, 'lock': threading.Lock()}

    try:
        out_handle = open(args.output, 'a', encoding='utf-8')
    except OSError as e:
        log("Cannot open " + args.output + ": " + str(e), Fore.RED)
        return 1
    results_handle = None
    if args.results_file:
        try:
            import os
            empty = not os.path.exists(args.results_file)
            results_handle = open(args.results_file, 'a', encoding='utf-8', newline='')
            if empty and args.results_format == 'csv':
                results_handle.write('username,code,latency_ms,message\n')
        except Exception as e:
            log('Cannot open results file: ' + str(e), Fore.RED)
            out_handle.close()
            return 1
    if args.no_cache:
        checked_handle = None
    else:
        try:
            checked_handle = open(args.checked_file, 'a', encoding='utf-8')
        except Exception as e:
            print('Cannot open checked file: ' + str(e))
            checked_handle = None

    if args.generate <= 0:
        try:
            open(args.input, 'r', encoding='utf-8').close()
        except FileNotFoundError:
            log('Input file ' + args.input + ' not found.', Fore.RED)
            out_handle.close()
            if results_handle:
                results_handle.close()
            if checked_handle:
                checked_handle.close()
            close_all_sessions()
            return 1

    log('Checking with ' + str(args.workers) + ' workers...', Fore.CYAN)
    result = _run(args, seen, stats, file_lock, results_lock, out_handle, results_handle, checked_handle, name_stream)
    for h in (out_handle, results_handle, checked_handle):
        try:
            if h:
                h.close()
        except:
            pass
    close_all_sessions()
    with stats['lock']:
        s2 = {k: v for k, v in stats.items() if k != 'lock'}
    log('Done: ' + str(result['total']) + ' checked -> ' + args.output + ' | ' + str(s2), Fore.GREEN)
    return result['ret']


if __name__ == '__main__':
    raise SystemExit(main())
