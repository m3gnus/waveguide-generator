/*
 * The macOS bundle's main executable.
 *
 * WHY THIS IS COMPILED RATHER THAN A SHELL SCRIPT.
 *
 * It used to be a bash script, which made the bundle a "script bundle":
 * `codesign -dv` reported `Format=app bundle with generic` rather than a normal
 * app. Gatekeeper does not offer such a bundle the ordinary unsigned-app
 * treatment. Measured on 2026-08-27 against a locally built .dmg, with a
 * quarantine attribute applied to simulate a download:
 *
 *     quarantined, launched directly   killed by the kernel, exit 137 (SIGKILL)
 *     quarantined, `open` (Finder)     blocked, no process
 *     quarantine cleared               runs normally
 *
 * So the app was unopenable exactly as downloaded, which is the only way a user
 * ever gets it. A Mach-O main executable puts the bundle back on the normal
 * path, where "Apple could not verify..." is followed by an Open Anyway control
 * in System Settings that actually works.
 *
 * This is NOT a substitute for notarization. It makes the documented override
 * work; a Developer ID signature plus notarization is what removes the prompt.
 *
 * It replicates the previous script exactly, minus one step: the script re-execed
 * itself under `arch -arm64` when LaunchServices started it translated, because a
 * script has no architecture of its own. A compiled arm64 binary cannot be
 * started translated, so the problem it solved cannot arise.
 */

#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

/* Exit codes distinct from anything Python returns, so a launcher failure is
 * never mistaken for an application failure. */
#define EXIT_NO_PATH 70
#define EXIT_NO_EXEC 71

static void fail(const char *what) {
    fprintf(stderr, "Waveguide Generator launcher: %s\n", what);
}

static int is_directory(const char *path) {
    struct stat info;
    return stat(path, &info) == 0 && S_ISDIR(info.st_mode);
}

static int is_regular_file(const char *path) {
    struct stat info;
    return stat(path, &info) == 0 && S_ISREG(info.st_mode);
}

/*
 * Reach recovery when a layer is the thing that is missing.
 *
 * The updater renames each layer aside and puts the staged copy in its place,
 * one layer at a time. Killed between those two renames it leaves either no
 * `app` or no `runtime` -- and everything this launcher used to be able to do
 * next lived in one of them: the application in `app`, and the interpreter that
 * runs it in `runtime`. So the recovery route is staged beside both, at
 * Resources/recovery, and this runs it with whichever of runtime/ and
 * runtime.previous/ survived. Nothing is taken from PATH or from the user's
 * data directory; every path below is built from the resolved bundle and from
 * nothing else.
 *
 * Run as a child rather than exec'd, because the launcher still has a job
 * afterwards: if recovery put the layers back, this start continues into them.
 * Returns the child's exit status, which is the verdict -- a directory back in
 * place is not a completed recovery, and the entry knows the difference.
 */
static int attempt_recovery(const char *resources, int argc, char *argv[]) {
    char entry[PATH_MAX];
    snprintf(entry, sizeof(entry), "%s/recovery/wg_bundle_recovery.py", resources);
    if (!is_regular_file(entry)) {
        return 0;
    }

    char interpreter[PATH_MAX];
    snprintf(interpreter, sizeof(interpreter), "%s/runtime/bin/python3.13", resources);
    if (access(interpreter, X_OK) != 0) {
        snprintf(interpreter, sizeof(interpreter),
                 "%s/runtime.previous/bin/python3.13", resources);
        if (access(interpreter, X_OK) != 0) {
            return 0;
        }
    }

    /* interpreter entry --resources <resources> -- [caller's arguments...] */
    char **args = calloc((size_t)argc + 6, sizeof(char *));
    if (args == NULL) {
        return 0;
    }
    int next = 0;
    args[next++] = interpreter;
    args[next++] = entry;
    args[next++] = "--resources";
    args[next++] = (char *)resources;
    args[next++] = "--";
    for (int i = 1; i < argc; i++) {
        args[next++] = argv[i];
    }
    args[next] = NULL;

    pid_t child = fork();
    if (child == 0) {
        execv(interpreter, args);
        _exit(127);
    }
    free(args);
    if (child < 0) {
        return 0;
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        /* Interrupted by a signal; the child is still the one to wait for. */
    }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

int main(int argc, char *argv[]) {
    char executable[PATH_MAX];
    uint32_t size = (uint32_t)sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) {
        fail("could not locate the executable");
        return EXIT_NO_PATH;
    }

    /* realpath resolves the symlinks a bundle may be reached through, so the
     * Resources directory is found relative to where the app really is. */
    char resolved[PATH_MAX];
    if (realpath(executable, resolved) == NULL) {
        fail("could not resolve the executable path");
        return EXIT_NO_PATH;
    }

    /* .../Contents/MacOS/<name> -> .../Contents */
    char macos_dir[PATH_MAX];
    snprintf(macos_dir, sizeof(macos_dir), "%s", resolved);
    char contents[PATH_MAX];
    snprintf(contents, sizeof(contents), "%s", dirname(macos_dir));
    char contents_copy[PATH_MAX];
    snprintf(contents_copy, sizeof(contents_copy), "%s", contents);
    char bundle_contents[PATH_MAX];
    snprintf(bundle_contents, sizeof(bundle_contents), "%s", dirname(contents_copy));

    char resources[PATH_MAX];
    snprintf(resources, sizeof(resources), "%s/Resources", bundle_contents);

    char app_root[PATH_MAX];
    snprintf(app_root, sizeof(app_root), "%s/app", resources);

    char interpreter[PATH_MAX];
    snprintf(interpreter, sizeof(interpreter), "%s/runtime/bin/python3.13", resources);

    setenv("WG2_BUNDLE", "1", 1);
    setenv("WG2_APP_ROOT", app_root, 1);

    /* The bundle is code-signed and must stay byte-identical after it runs, so
     * bytecode and numba kernel caches go to the user's cache directory rather
     * than beside the sources they belong to. Writing into the bundle would
     * break its seal and make the next launch fail as damaged. */
    const char *home = getenv("HOME");
    if (home != NULL && home[0] != '\0') {
        char cache_root[PATH_MAX];
        snprintf(cache_root, sizeof(cache_root), "%s/Library/Caches/WaveguideGenerator", home);
        char pycache[PATH_MAX];
        snprintf(pycache, sizeof(pycache), "%s/pycache", cache_root);
        char numba[PATH_MAX];
        snprintf(numba, sizeof(numba), "%s/numba", cache_root);
        setenv("PYTHONPYCACHEPREFIX", pycache, 1);
        setenv("NUMBA_CACHE_DIR", numba, 1);
    }

    /* Either layer can be the missing one, because the updater takes them one
     * at a time: killed inside the runtime's turn, `app` is the new generation
     * and there is no interpreter to run it with. Checking only for `app` would
     * walk straight into an exec of a file that is not there. */
    if (!is_directory(app_root) || access(interpreter, X_OK) != 0) {
        if (!attempt_recovery(resources, argc, argv)
            || !is_directory(app_root)
            || access(interpreter, X_OK) != 0) {
            fail("this installation is incomplete and could not be recovered; "
                 "reinstall this version over the top -- your designs and "
                 "settings live outside the application and are not touched");
            return EXIT_NO_EXEC;
        }
    }

    if (chdir(app_root) != 0) {
        fail("could not enter the application directory");
        return EXIT_NO_EXEC;
    }

    /* argv: interpreter -m launchers.desktop [caller's arguments...] NULL */
    char **args = calloc((size_t)argc + 3, sizeof(char *));
    if (args == NULL) {
        fail("out of memory");
        return EXIT_NO_EXEC;
    }
    int next = 0;
    args[next++] = interpreter;
    args[next++] = "-m";
    args[next++] = "launchers.desktop";
    for (int i = 1; i < argc; i++) {
        args[next++] = argv[i];
    }
    args[next] = NULL;

    execv(interpreter, args);

    /* execv only returns on failure. */
    fail("could not start the bundled interpreter");
    free(args);
    return EXIT_NO_EXEC;
}
