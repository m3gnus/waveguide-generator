/* Independent POSIX diagnostics: no bundled runtime or pathname reopen. */
#define _DARWIN_C_SOURCE
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <sys/select.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define LIMIT 262144
#define LINE 8192
#define DEADLINE 5000
#define KILL_GRACE 250
static const char header[] = "[WG installer output]\n";
static const char note[] = "[Earlier output retained in install.log.1]\n";
static int active = -1, previous = -1;
static char *active_path, *previous_path;
static struct stat active_info, previous_info;
static unsigned char content[LIMIT];
static size_t used;

static long long millis(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (long long)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}
static int write_all(int fd, const void *data, size_t size) {
    const unsigned char *bytes = data;
    while (size) {
        ssize_t count = write(fd, bytes, size);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        bytes += count; size -= (size_t)count;
    }
    return 0;
}
static int capture(const char *path, struct stat *identity) {
    /* Existing objects are opened without O_CREAT. A dangling symlink cannot
       create its referent and a FIFO cannot block this capture. */
    int fd = open(path, O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0 && errno == ENOENT) {
        fd = open(path, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0600);
        if (fd >= 0 && write_all(fd, header, sizeof(header) - 1) != 0) { close(fd); return -1; }
    }
    if (fd < 0) return -1;
    char first[sizeof(header) - 1];
    if (fstat(fd, identity) != 0 || !S_ISREG(identity->st_mode) || identity->st_nlink != 1 ||
        identity->st_size < (off_t)sizeof(first) || identity->st_size > LIMIT ||
        pread(fd, first, sizeof(first), 0) != (ssize_t)sizeof(first) ||
        memcmp(first, header, sizeof(first)) != 0) { close(fd); return -1; }
    return fd;
}
static int owned(int fd, const char *path, const struct stat *saved) {
    struct stat held, named;
    return fstat(fd, &held) == 0 && lstat(path, &named) == 0 &&
        S_ISREG(held.st_mode) && held.st_nlink == 1 && S_ISREG(named.st_mode) &&
        held.st_dev == saved->st_dev && held.st_ino == saved->st_ino &&
        named.st_dev == held.st_dev && named.st_ino == held.st_ino;
}
static int replace(int fd, const char *path, const struct stat *saved,
                   const void *data, size_t size) {
    if (size > LIMIT || !owned(fd, path, saved)) return -1;
    if (ftruncate(fd, 0) != 0 || lseek(fd, 0, SEEK_SET) < 0) return -1;
    return write_all(fd, data, size) != 0 || !owned(fd, path, saved) ? -1 : 0;
}
static int line(const unsigned char *value, size_t size) {
    if (!owned(active, active_path, &active_info) || !owned(previous, previous_path, &previous_info)) return -1;
    if (used + size + 1 > LIMIT) {
        if (replace(previous, previous_path, &previous_info, content, used) != 0) return -1;
        memcpy(content, header, sizeof(header) - 1);
        memcpy(content + sizeof(header) - 1, note, sizeof(note) - 1);
        used = sizeof(header) + sizeof(note) - 2;
        if (replace(active, active_path, &active_info, content, used) != 0) return -1;
    }
    if (lseek(active, 0, SEEK_END) < 0 || write_all(active, value, size) != 0 || write_all(active, "\n", 1) != 0)
        return -1;
    memcpy(content + used, value, size); used += size; content[used++] = '\n';
    if (getenv("WG_INSTALLER_LOG_ECHO") && strcmp(getenv("WG_INSTALLER_LOG_ECHO"), "1") == 0 &&
        (write_all(STDERR_FILENO, value, size) != 0 || write_all(STDERR_FILENO, "\n", 1) != 0)) return -1;
    return 0;
}
static int sink(void) {
    active = capture(active_path, &active_info);
    previous = capture(previous_path, &previous_info);
    if (active < 0 || previous < 0) return 72;
    used = (size_t)active_info.st_size;
    if (pread(active, content, used, 0) != (ssize_t)used) return 72;
    unsigned char block[LINE], value[LINE];
    size_t length = 0;
    for (;;) {
        ssize_t count = read(STDIN_FILENO, block, sizeof(block));
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) return 72;
        if (!count) break;
        for (ssize_t i = 0; i < count; i++) {
            if (block[i] == '\n') {
                if (line(value, length) != 0) return 72;
                length = 0;
            } else if (length < sizeof(value)) value[length++] = block[i];
        }
    }
    if (length && line(value, length) != 0) return 72;
    return 0;
}
static int published(const char *path) {
    /* The wrapper must record this actual unreaped PID before we fork a sink.
       Record failure therefore cannot strand an unrecorded descendant. */
    long long deadline = millis() + DEADLINE;
    do {
        int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
        if (fd >= 0) {
            struct stat info; char text[33] = {0};
            if (fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) || info.st_size > 32) { close(fd); return -1; }
            ssize_t size = read(fd, text, 32); close(fd);
            char expected[33]; snprintf(expected, sizeof(expected), "%ld\n", (long)getpid());
            if (size > 0 && strcmp(text, expected) == 0) return 0;
        } else if (errno != ENOENT) return -1;
        struct timespec pause = {0, 10000000}; nanosleep(&pause, NULL);
    } while (millis() < deadline);
    return -1;
}
int main(int argc, char **argv) {
    if (!getenv("WG_INSTALLER_LOG")) return 72;
    active_path = getenv("WG_INSTALLER_LOG");
    previous_path = malloc(strlen(active_path) + 3);
    if (!previous_path || millis() < 0) return 72;
    sprintf(previous_path, "%s.1", active_path);
    if (argc == 2 && strcmp(argv[1], "--check") == 0) {
        active = capture(active_path, &active_info);
        previous = capture(previous_path, &previous_info);
        int result = active < 0 || previous < 0 ? 72 : 0;
        if (active >= 0) close(active);
        if (previous >= 0) close(previous);
        free(previous_path);
        return result;
    }
    if (argc != 3 || strcmp(argv[1], "--writer-record") != 0 || published(argv[2]) != 0) return 72;
    char *sink_path = malloc(strlen(argv[2]) + 6);
    if (!sink_path) return 72;
    strcpy(sink_path, argv[2]);
    char *leaf = strrchr(sink_path, '/');
    if (!leaf || strcmp(leaf + 1, "writer") != 0) return 72;
    strcpy(leaf + 1, "sink");
    /* An empty sink record is deliberately ambiguous after a hard kill.
       The sink waits for durable actual-PID publication before touching logs. */
    int sink_record = open(sink_path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0600);
    if (sink_record < 0) return 72;
    struct stat sink_record_info;
    if (fstat(sink_record, &sink_record_info) != 0) return 72;
    int channel[2], permit[2];
    if (pipe(channel) != 0 || pipe(permit) != 0) return 72;
    if (signal(SIGCHLD, SIG_DFL) == SIG_ERR) return 72;
    signal(SIGPIPE, SIG_IGN);
    pid_t child = fork();
    if (child < 0) return 72;
    if (!child) {
        close(sink_record); close(permit[1]);
        char allowed;
        if (read(permit[0], &allowed, 1) != 1 || allowed != '1') _exit(72);
        close(permit[0]);
        close(channel[1]);
        if (dup2(channel[0], STDIN_FILENO) < 0) _exit(72);
        close(channel[0]);
        _exit(sink());
    }
    close(channel[0]); close(permit[0]);
    int failed = 0;
    char sink_pid[33];
    int sink_size = snprintf(sink_pid, sizeof(sink_pid), "%ld\n", (long)child);
    if (!owned(sink_record, sink_path, &sink_record_info) ||
        write_all(sink_record, sink_pid, (size_t)sink_size) != 0 || fsync(sink_record) != 0 ||
        !owned(sink_record, sink_path, &sink_record_info) || write_all(permit[1], "1", 1) != 0) {
        failed = 1;
    }
    close(sink_record); close(permit[1]); free(sink_path);
    if (fcntl(channel[1], F_SETFL, O_NONBLOCK) != 0) failed = 1;
    /* The supervisor owns the FIFO reader continuously and never writes to
       the sink synchronously. Stalled/failed diagnostics switch to discard. */
    unsigned char buffer[LINE]; size_t pending = 0, offset = 0;
    int ended = 0, reaped = 0, status = 0, signalled = 0, reap_error = 0;
    long long kill_deadline = 0;
    long long progress = millis();
    while (!ended || pending) {
        if (!reaped && !reap_error) {
            pid_t result = waitpid(child, &status, WNOHANG);
            if (result == child) { reaped = 1; failed = 1; }
            else if (result < 0 && errno != EINTR) { reap_error = 1; failed = 1; }
        }
        if (pending && millis() - progress >= DEADLINE) failed = 1;
        if (failed && channel[1] >= 0) {
            close(channel[1]); channel[1] = -1; pending = 0;
        }
        if (failed && !reaped && !reap_error && !signalled) {
            /* A captured unreaped child cannot have a reused PID. Killing it
               need not settle blocked file I/O: keep draining the native FIFO. */
            kill(child, SIGKILL);
            signalled = 1; kill_deadline = millis() + KILL_GRACE;
        }
        fd_set readable, writable;
        FD_ZERO(&readable); FD_ZERO(&writable);
        if (!ended && !pending) FD_SET(STDIN_FILENO, &readable);
        if (pending) FD_SET(channel[1], &writable);
        struct timeval pause = {0, 100000};
        int ready = select(channel[1] > STDIN_FILENO ? channel[1] + 1 : STDIN_FILENO + 1,
                           &readable, &writable, NULL, &pause);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) { failed = 1; ended = 1; continue; }
        if (!ended && !pending && FD_ISSET(STDIN_FILENO, &readable)) {
            ssize_t count = read(STDIN_FILENO, buffer, sizeof(buffer));
            if (count > 0 && !failed) { pending = (size_t)count; offset = 0; progress = millis(); }
            else if (count <= 0) ended = 1;
        }
        if (pending && FD_ISSET(channel[1], &writable)) {
            ssize_t count = write(channel[1], buffer + offset, pending);
            if (count > 0) { pending -= (size_t)count; offset += (size_t)count; progress = millis(); }
            else if (count < 0 && errno != EINTR && errno != EAGAIN) failed = 1;
        }

    }
    if (channel[1] >= 0) close(channel[1]);
    long long deadline = millis() + DEADLINE;
    while (!reaped && !reap_error) {
        pid_t result = waitpid(child, &status, WNOHANG);
        if (result == child) { reaped = 1; break; }
        if (result < 0 && errno != EINTR) { reap_error = 1; failed = 1; break; }
        if (signalled && millis() >= kill_deadline) break;
        if (!signalled && millis() >= deadline) {
            failed = 1;
            kill(child, SIGKILL);
            signalled = 1; kill_deadline = millis() + KILL_GRACE;
        }
        struct timespec pause = {0, 10000000}; nanosleep(&pause, NULL);
    }
    free(previous_path);
    /* An unsettled/ambiguous sink keeps its durable ownership record. The
       wrapper can preserve exclusion without changing the native decision. */
    return failed || !reaped || !WIFEXITED(status) || WEXITSTATUS(status) != 0 ? 72 : 0;
}
