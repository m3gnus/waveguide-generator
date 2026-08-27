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
#include <unistd.h>

/* Exit codes distinct from anything Python returns, so a launcher failure is
 * never mistaken for an application failure. */
#define EXIT_NO_PATH 70
#define EXIT_NO_EXEC 71

static void fail(const char *what) {
    fprintf(stderr, "Waveguide Generator launcher: %s\n", what);
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
