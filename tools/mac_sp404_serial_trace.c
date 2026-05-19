// mac_sp404_serial_trace.c
//
// DYLD interposer for capturing SP-404MKII librarian serial traffic on macOS.
// Build on the Mac:
//   clang -dynamiclib -O2 -Wall -o libsp404_serial_trace.dylib mac_sp404_serial_trace.c
//
// Launch the Roland app executable directly with SP404_TRACE_LOG and
// DYLD_INSERT_LIBRARIES set, targeting the app executable under
// /Applications/Roland/SP-404MKII.app/Contents/MacOS/.

#define _DARWIN_C_SOURCE

#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <unistd.h>

#ifndef O_CREAT
#define O_CREAT 0x0200
#endif

#define MAX_FD 4096
#define MAX_CAPTURE 1024

static int (*real_open_fn)(const char *, int, ...) = NULL;
static int (*real_openat_fn)(int, const char *, int, ...) = NULL;
static int (*real_close_fn)(int) = NULL;
static ssize_t (*real_read_fn)(int, void *, size_t) = NULL;
static ssize_t (*real_write_fn)(int, const void *, size_t) = NULL;
static int (*real_ioctl_fn)(int, unsigned long, ...) = NULL;

static pthread_mutex_t log_lock = PTHREAD_MUTEX_INITIALIZER;
static bool watched_fd[MAX_FD];
static int log_fd = -1;

static void resolve_symbols(void) {
    if (!real_open_fn) real_open_fn = dlsym(RTLD_NEXT, "open");
    if (!real_openat_fn) real_openat_fn = dlsym(RTLD_NEXT, "openat");
    if (!real_close_fn) real_close_fn = dlsym(RTLD_NEXT, "close");
    if (!real_read_fn) real_read_fn = dlsym(RTLD_NEXT, "read");
    if (!real_write_fn) real_write_fn = dlsym(RTLD_NEXT, "write");
    if (!real_ioctl_fn) real_ioctl_fn = dlsym(RTLD_NEXT, "ioctl");
}

static bool is_sp404_serial_path(const char *path) {
    if (!path) return false;
    return strstr(path, "usbmodem") != NULL ||
           strstr(path, "tty.usb") != NULL ||
           strstr(path, "cu.usb") != NULL;
}

static void append_raw(const char *s, size_t n) {
    if (log_fd >= 0 && real_write_fn) {
        (void)real_write_fn(log_fd, s, n);
    }
}

static void append_str(const char *s) {
    append_raw(s, strlen(s));
}

static void append_json_string(const char *s) {
    append_str("\"");
    if (s) {
        for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
            char buf[8];
            if (*p == '"' || *p == '\\') {
                buf[0] = '\\';
                buf[1] = (char)*p;
                append_raw(buf, 2);
            } else if (*p >= 0x20 && *p < 0x7f) {
                append_raw((const char *)p, 1);
            } else {
                snprintf(buf, sizeof(buf), "\\u%04x", *p);
                append_str(buf);
            }
        }
    }
    append_str("\"");
}

static void append_hex(const void *data, size_t n) {
    static const char hex[] = "0123456789abcdef";
    const unsigned char *p = (const unsigned char *)data;
    for (size_t i = 0; i < n; ++i) {
        char b[3];
        b[0] = hex[p[i] >> 4];
        b[1] = hex[p[i] & 0x0f];
        b[2] = (i + 1 == n) ? '\0' : ' ';
        append_raw(b, b[2] ? 3 : 2);
    }
}

static void log_event(const char *event, int fd, ssize_t len,
                      const void *data, size_t capture_len,
                      const char *path, unsigned long request) {
    if (log_fd < 0) return;
    struct timeval tv;
    gettimeofday(&tv, NULL);

    pthread_mutex_lock(&log_lock);
    append_str("{\"ts\":");
    char buf[128];
    snprintf(buf, sizeof(buf), "%lld.%03d",
             (long long)tv.tv_sec, (int)(tv.tv_usec / 1000));
    append_str(buf);
    append_str(",\"event\":");
    append_json_string(event);
    append_str(",\"fd\":");
    snprintf(buf, sizeof(buf), "%d", fd);
    append_str(buf);
    if (len >= 0) {
        append_str(",\"len\":");
        snprintf(buf, sizeof(buf), "%zd", len);
        append_str(buf);
    }
    if (request != 0) {
        append_str(",\"request\":\"0x");
        snprintf(buf, sizeof(buf), "%lx", request);
        append_str(buf);
        append_str("\"");
    }
    if (path) {
        append_str(",\"path\":");
        append_json_string(path);
    }
    if (data && capture_len > 0) {
        append_str(",\"hex\":\"");
        append_hex(data, capture_len);
        append_str("\"");
    }
    append_str("}\n");
    pthread_mutex_unlock(&log_lock);
}

__attribute__((constructor))
static void sp404_trace_init(void) {
    resolve_symbols();
    const char *path = getenv("SP404_TRACE_LOG");
    if (!path || !*path) path = "/tmp/sp404_serial_trace.jsonl";
    if (real_open_fn) {
        log_fd = real_open_fn(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    }
    log_event("trace_start", -1, -1, NULL, 0, path, 0);
}

__attribute__((destructor))
static void sp404_trace_done(void) {
    log_event("trace_stop", -1, -1, NULL, 0, NULL, 0);
    if (log_fd >= 0 && real_close_fn) {
        int fd = log_fd;
        log_fd = -1;
        real_close_fn(fd);
    }
}

int traced_open(const char *path, int flags, ...) {
    resolve_symbols();
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }
    int fd = (flags & O_CREAT) ? real_open_fn(path, flags, mode)
                               : real_open_fn(path, flags);
    if (fd >= 0 && fd < MAX_FD && is_sp404_serial_path(path)) {
        watched_fd[fd] = true;
        log_event("open", fd, -1, NULL, 0, path, 0);
    }
    return fd;
}

int traced_openat(int dirfd, const char *path, int flags, ...) {
    resolve_symbols();
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }
    int fd = (flags & O_CREAT) ? real_openat_fn(dirfd, path, flags, mode)
                               : real_openat_fn(dirfd, path, flags);
    if (fd >= 0 && fd < MAX_FD && is_sp404_serial_path(path)) {
        watched_fd[fd] = true;
        log_event("openat", fd, -1, NULL, 0, path, 0);
    }
    return fd;
}

int traced_close(int fd) {
    resolve_symbols();
    if (fd >= 0 && fd < MAX_FD && watched_fd[fd]) {
        log_event("close", fd, -1, NULL, 0, NULL, 0);
        watched_fd[fd] = false;
    }
    return real_close_fn(fd);
}

ssize_t traced_write(int fd, const void *buf, size_t count) {
    resolve_symbols();
    if (fd >= 0 && fd < MAX_FD && watched_fd[fd] && count > 0) {
        size_t cap = count > MAX_CAPTURE ? MAX_CAPTURE : count;
        log_event("tx", fd, (ssize_t)count, buf, cap, NULL, 0);
    }
    return real_write_fn(fd, buf, count);
}

ssize_t traced_read(int fd, void *buf, size_t count) {
    resolve_symbols();
    ssize_t n = real_read_fn(fd, buf, count);
    if (fd >= 0 && fd < MAX_FD && watched_fd[fd] && n > 0) {
        size_t cap = (size_t)n > MAX_CAPTURE ? MAX_CAPTURE : (size_t)n;
        log_event("rx", fd, n, buf, cap, NULL, 0);
    }
    return n;
}

int traced_ioctl(int fd, unsigned long request, ...) {
    resolve_symbols();
    va_list ap;
    va_start(ap, request);
    void *argp = va_arg(ap, void *);
    va_end(ap);
    if (fd >= 0 && fd < MAX_FD && watched_fd[fd]) {
        log_event("ioctl", fd, -1, NULL, 0, NULL, request);
    }
    return real_ioctl_fn(fd, request, argp);
}

#define DYLD_INTERPOSE(_replacement, _replacee) \
    __attribute__((used)) static struct { const void *replacement; const void *replacee; } \
    _interpose_##_replacee __attribute__((section("__DATA,__interpose"))) = { \
        (const void *)(unsigned long)&_replacement, \
        (const void *)(unsigned long)&_replacee \
    };

DYLD_INTERPOSE(traced_open, open)
DYLD_INTERPOSE(traced_openat, openat)
DYLD_INTERPOSE(traced_close, close)
DYLD_INTERPOSE(traced_write, write)
DYLD_INTERPOSE(traced_read, read)
DYLD_INTERPOSE(traced_ioctl, ioctl)
