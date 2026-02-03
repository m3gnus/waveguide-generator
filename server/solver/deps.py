try:
    import gmsh
    GMSH_AVAILABLE = True
except ImportError:
    GMSH_AVAILABLE = False
    gmsh = None

try:
    # bempp-cl 0.4+ uses bempp_cl module name
    import bempp_cl.api as bempp_api
    BEMPP_AVAILABLE = True
except ImportError:
    try:
        # Older versions use bempp_api
        import bempp_api as bempp_api
        BEMPP_AVAILABLE = True
    except ImportError:
        BEMPP_AVAILABLE = False
        bempp_api = None
        print("Warning: bempp-cl not available")
