"""
MWG Horn BEM Solver Backend
FastAPI application for running acoustic simulations
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import uuid
import asyncio
from datetime import datetime

# Import solver module (will be created)
try:
    from solver import BEMSolver
    SOLVER_AVAILABLE = True
except ImportError:
    SOLVER_AVAILABLE = False
    print("Warning: BEM solver not available. Install bempp-cl to enable simulations.")

app = FastAPI(title="MWG Horn BEM Solver", version="1.0.0")

# Enable CORS for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Job storage (in production, use Redis or database)
jobs: Dict[str, Dict[str, Any]] = {}


class BoundaryCondition(BaseModel):
    type: str  # 'velocity', 'neumann', 'robin'
    surfaceTag: int
    value: Optional[float] = None
    impedance: Optional[str] = None


class MeshData(BaseModel):
    vertices: List[float]
    indices: List[int]
    surfaceTags: Optional[List[int]] = None  # Per-triangle surface tags
    format: str = "bem"
    boundaryConditions: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class SimulationRequest(BaseModel):
    mesh: MeshData
    frequency_range: List[float]
    num_frequencies: int
    sim_type: str
    options: Optional[Dict[str, Any]] = {}


class JobStatus(BaseModel):
    status: str
    progress: float
    message: Optional[str] = None


class SimulationResults(BaseModel):
    frequencies: List[float]
    directivity: Dict[str, List[List[float]]]
    impedance: Optional[Dict[str, List[float]]] = None
    spl_on_axis: Optional[Dict[str, List[float]]] = None
    di: Optional[Dict[str, List[float]]] = None


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "MWG Horn BEM Solver",
        "version": "1.0.0",
        "status": "running",
        "solver_available": SOLVER_AVAILABLE
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "solver": "bempp-cl" if SOLVER_AVAILABLE else "unavailable",
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/solve")
async def submit_simulation(request: SimulationRequest):
    """
    Submit a new BEM simulation job
    
    Returns a job ID for tracking progress
    """
    if not SOLVER_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="BEM solver not available. Please install bempp-cl."
        )
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Initialize job status
    jobs[job_id] = {
        "status": "queued",
        "progress": 0.0,
        "created_at": datetime.now().isoformat(),
        "request": request.dict(),
        "results": None,
        "error": None
    }
    
    # Start simulation in background
    asyncio.create_task(run_simulation(job_id, request))
    
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def get_job_status(job_id: str):
    """
    Get the status of a simulation job
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    return JobStatus(
        status=job["status"],
        progress=job["progress"],
        message=job.get("error")
    )


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    """
    Retrieve simulation results
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    
    if job["status"] != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Job not complete. Current status: {job['status']}"
        )
    
    if job["results"] is None:
        raise HTTPException(status_code=500, detail="Results not available")
    
    return job["results"]


async def run_simulation(job_id: str, request: SimulationRequest):
    """
    Run BEM simulation in background
    """
    try:
        # Update status
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = 0.1
        
        # Initialize solver
        solver = BEMSolver()
        
        # Convert mesh data with surface tags
        jobs[job_id]["progress"] = 0.2
        mesh = solver.prepare_mesh(
            request.mesh.vertices,
            request.mesh.indices,
            surface_tags=request.mesh.surfaceTags,
            boundary_conditions=request.mesh.boundaryConditions
        )
        
        # Run simulation
        jobs[job_id]["progress"] = 0.3
        
        results = await asyncio.to_thread(
            solver.solve,
            mesh=mesh,
            frequency_range=request.frequency_range,
            num_frequencies=request.num_frequencies,
            sim_type=request.sim_type,
            progress_callback=lambda p: update_progress(job_id, 0.3 + p * 0.6)
        )
        
        # Store results
        jobs[job_id]["progress"] = 1.0
        jobs[job_id]["status"] = "complete"
        jobs[job_id]["results"] = results
        jobs[job_id]["completed_at"] = datetime.now().isoformat()
        
    except Exception as e:
        import traceback
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["failed_at"] = datetime.now().isoformat()
        print(f"Simulation error for job {job_id}: {e}")
        print(f"Full traceback:")
        traceback.print_exc()


def update_progress(job_id: str, progress: float):
    """Update job progress"""
    if job_id in jobs:
        jobs[job_id]["progress"] = min(0.95, progress)


if __name__ == "__main__":
    import uvicorn
    print("Starting MWG Horn BEM Solver Backend...")
    print(f"Solver available: {SOLVER_AVAILABLE}")
    if not SOLVER_AVAILABLE:
        print("Warning: bempp-cl not installed. Install it to enable simulations.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
