# Finish a speaker in Fusion and solve it in WG

Use WG to design the waveguide, add the rest of the speaker in Autodesk Fusion,
then send the finished geometry to WG for an acoustic solve. You can also start
with a model drawn entirely in Fusion. WG solves the geometry you send; CAD
edits do not turn into editable waveguide parameters in WG.

This guide covers the current WG and the Waveguide Generator add-in for Fusion
that ships with it. Fusion runs on Windows and macOS. For a short step-by-step
introduction, read the [user manual](USER-MANUAL.md) first. The
[general user guide](USER-GUIDE.md) covers launching WG and the results
viewer. The add-in's own guide is in the `wglink` repository
(`docs/WGLINK-GUIDE.md`).

The three Fusion commands, and what they mean:

| Command | Meaning |
|---|---|
| **Sources & Mesh…** | Set up sources and mesh, then Send or Solve. |
| **Send to WG** | Open this model in WG to inspect or set up. Does not solve. |
| **Solve in WG** | Send and solve now. |

## 1. Install and enable the add-in

1. Install Fusion and WG on the same computer. Close Fusion while the add-in
   is installed or replaced.
2. On Windows, tick the add-in task in WG's installer. It is preselected when
   Fusion's AddIns folder exists; a silent install opts in with
   `/TASKS="wglink"`. Setup reports whether the add-in was installed, updated,
   not selected, not detected, preserved or failed.
3. WG installs and updates the add-in at startup only if you chose it in
   setup, installed it from WG, or have already received a model from Fusion.
   Otherwise use **Install add-in** on the **CAD Link** panel (it appears when
   the add-in is missing or out of date), with Fusion closed. It also repairs
   a damaged copy. No separate Python installation is needed.
4. Start Fusion. Under **Utilities → Scripts and Add-Ins → Add-Ins**, select
   **WGLink**, run it, and tick **Run on Startup**. The commands appear in the
   **Waveguide Generator** panel under **Utilities**. Keep only one **WGLink**
   entry.
5. In WG, open **Settings → CAD Link** and check the **WGLink folder**. The
   default WG folder is `Documents/Waveguide Generator`: its `cadlink/` child
   is the WGLink folder and its `runs/` child is the Runs folder. A missing
   WGLink folder offers **Create it**. Fusion reads the same setting, so you
   never set a path in Fusion.

If WG says activation of the add-in is pending until Fusion closes, close
Fusion and leave WG running, then reopen Fusion. WG leaves developer copies,
symlinked copies and copies owned by another WG installation alone. It also
reports duplicate registrations, a copy registered from another folder, or a
missing **Run on Startup**; fix those in Fusion's add-in list.

## 2. Send a WG waveguide to Fusion

1. Finish the basic waveguide in WG. Open the design menu (the design name at
   the top left) and choose **Export → Send to CAD**. On the **CAD Link**
   panel, **Open in Fusion 360** does the same. A completed parametric run's
   export menu also has **Send to CAD**.
2. WG writes the design and starts or raises Fusion, and the add-in inserts
   it. If it does not, use **Manage Link… → Insert** in Fusion.
3. Save and name the Fusion document. That name identifies its runs in WG.
4. Add the flange, baffle, enclosure, woofer openings and other passages with
   Fusion's normal tools. Keep every surface the sound meets in the model.

The add-in inserts native Fusion features with throat and mouth sketches and
datums. Build your features from these references where you can, and move or
joint the whole inserted component, not the bodies inside it. If you change the
waveguide in WG later, send it again and use **Update** in Fusion, then check
your own features.

A model in WG is a snapshot. After editing in Fusion, use **Send to WG** or
**Solve in WG** again; the two apps do not stay in sync on their own.

## 3. Decide which bodies to send

1. In the Send or Solve dialog, leave **Assembly scope** empty to send the
   whole design, or select one occurrence (one placed copy of a component).
   Check the **Pre-flight** list: the included bodies and the drivers must
   describe the speaker you mean.
2. Use solid bodies for walls and baffles. A visible surface body that is part
   of the speaker needs **Manage Link… → Declare Body… → Exterior shell**.
   Mark modelling aids **Exclude** and hide them; a visible excluded body
   stops the Send.
3. Do not send air volumes as ordinary bodies: air is not a wall. Declare
   them as FEM regions (below) or hide them.
4. Check that hiding or excluding a body has not removed part of a driver.

Closed solids are the simplest start. An open shell has free edges, which some
solvers cannot take away from a mirror plane. A void sealed inside a solid can
also fail; open it to the outside or model it as separate shells.

## 4. Mark the drivers and set the mesh

Open **Waveguide Generator ▸ Sources & Mesh…** in Fusion.

1. **Drivers.** The table has one fixed row for each role: **HF**, **MF**,
   **LF** and **Port or passive opening**. Click a row's **Pick faces**, then
   click its faces in the model. One face picker below the tables fills
   whichever row you picked. A face picked for a second row moves there, and
   the dialog says so. **✕** clears a row. Leave unused rows empty. Drivers
   from a linked WG design are read-only. Pick the face that moves air (a
   throat or diaphragm), not a magnet or basket.
2. **Walls** sets the triangle size of every surface that is not a driver and
   not in a size zone.
3. **Size zones** make parts of the model finer or coarser than the walls.
   Click **+ Fine size zone (15 mm)** or **+ Coarse size zone (40 mm)**, name
   it, adjust its size, and pick its faces or whole bodies.
4. **Advanced** holds **Grading distance** (how far from each driver the mesh
   grows to the wall size), **Symmetry** (**Automatic** or **Off**) and
   **Reset to defaults**.
5. **Estimate** shows about how many triangles WG will solve, against WG's
   limit of 22,000. Before WG has seen the model it is a range, from a quarter
   model to the whole model, because WG decides whether it can use symmetry.
   After a solve it also shows the last count WG used.
6. Click **Save**, or **Cancel** to change nothing. Save stores the settings in
   the Fusion document; reopening the dialog restores them. Then use **Send to
   WG** or **Solve in WG**.

Defaults: HF 6 mm, MF 15 mm, LF and port 25 mm, walls 25 mm, grading distance
100 mm. Smaller sizes give more triangles.

The dialog marks each driver with a lasting identity, so later sends refer to
the same driver and WG keeps its setup. Body and component names do not mark
drivers. If you split, copy or delete a driver face, reopen Sources & Mesh…,
pick every face of that driver again and Save; WG then asks for that driver's
setup once more.

In WG, **Model details** on the **Imported mesh** tab shows the mesh as one
read-only line: "Mesh (set in Fusion)", the sizes, the triangle count and the
memory estimate. To change it, use Sources & Mesh… in Fusion and Send again.

**Side names.** Names such as `MFLeft`, `HF_R` or `woofer_L1` tell WG a driver
belongs to one side. WG will not mirror a model already cut in CAD if a driver
belongs to one side, because the mirror would stand in for the other driver.
Send the whole model when the sides need their own drivers.

## 5. Send or Solve

1. In Fusion, click **Send to WG** to set up first, or **Solve in WG** to
   solve now.
2. Check **Assembly scope** and the **Pre-flight** list. If several linked
   waveguides are included, **Solver anchor instance** chooses the one whose
   throat and direction define the solve's origin and axis. In WG, pick the
   same placement under **Managed Fusion link** on the **CAD Link** panel.
3. Pre-flight sorts what it finds three ways:
   - **Fix first.** Send and Solve stay disabled until you fix it, for
     example "This model has no driver yet." A model whose only driver is a
     FEM region's moving surface needs no driver row.
   - **Asked when you press OK.** A body that is not a closed solid asks
     whether to send anyway. A Solve whose smallest possible mesh is still
     over the limit asks: **Yes** solves anyway, **No** opens Sources & Mesh…
     to change the mesh. If you solve anyway here, WG does not ask again.
   - **Noted.** For example, a size zone whose faces are all outside the
     selected part is left out of that send.
4. Click **Send** or **Solve**.

The add-in's progress panel (titled **Waveguide Generator**, docked right)
opens on every Send or Solve. It follows the model through preparing, waiting
for the solver, solving with a frequency count ("Solving · 12 of 36
frequencies"), and solved, with **Show results in Waveguide Generator**. It
opens by itself when WG needs you. **Requests…** reopens it and lists recent
Sends and Solves for **This document** or **All documents**; **Clear
finished** tidies the list. If WG has not picked the model up yet, the panel
says so and offers **Send again**.

If WG is not running, the add-in starts it in the background where it can. If
that is not possible, it says why; start WG yourself and try again.

## 6. In WG: the Imported mesh tab

A model from Fusion appears on the **Imported mesh** tab with **CAD Link**
selected. WG prepares it automatically when it arrives, when you choose it
under **Model versions**, and when WG reopens it after a restart. There is no
prepare button.

Under the model's name is **one status line** with at most one button:

| The line says | Button |
|---|---|
| Preparing <name>… | none |
| Ready to solve · 3,035 triangles | **Solve** |
| <name> needs your choice: it meshes to 54,739 triangles (limit 22,000). | **Choose…** |
| Solving · 13 of 36 frequencies | none (Stop is on the run card) |
| Solved · run #1 · just now | **Show results** |
| Waveguide Generator could not prepare <name>. | **Try again**, when it can help |

**Solve** is also at the top right of the window. Results shows charts and
progress; it has no Solve button of its own for a CAD model.

**Model details** (folded; it opens by itself when something needs a look)
holds:

- what the model is and which drivers it has;
- how WG solves it: whole, half or quarter, and why;
- **Radiates along**, with **Change** (for models drawn in Fusion);
- the mesh line from Fusion;
- the sweep and solver in one line, with **Settings…**;
- the checks, with a plain line for any that need attention;
- FEM regions, when the model has them.

Below it: **Model versions** lists earlier sends, and **View project runs**
opens the **Jobs** panel filtered to this model. The connection line at the
bottom ("Fusion 360 · connected") holds **Settings** and, when needed,
**Install add-in**.

**Show developer details** (**Settings → Advanced**, off by default) adds
the technical detail behind these lines: check evidence, identifiers and the
raw text of a failure. You do not need it for normal work.

## 7. Set up the drivers in WG

Open the **Simulation** tab. For a CAD model it has:

- **Sweep** (open): start, end and number of frequencies.
- **Drivers** (open): one card per driver.
- **Crossover** (folded; only with two or more drivers).
- **Directivity** (folded, with a summary).
- **Passive cardioid** (only with a port or passive opening).
- **Solver** (folded, with a summary).

Before the model is ready the tab says "Settings appear when <name> is ready."

For each driver card:

1. Choose a driver from the library or enter its data by hand. Check the
   impedance variant and the datasheet values. A driver needs **Sd, Bl, Re**,
   one of **Mmd/Mms**, and one of **Cms/Vas/Fs**. If you enter **Le2** or
   **Re2**, enter both. Clear the driver to solve with a unit drive.
2. Set **Count** to the number of identical drivers sharing the source,
   wired in parallel and moving together, and **Rear volume** to their one
   shared sealed chamber, in litres. Enter per-driver data; do not multiply Sd
   or the volume by Count. Keep the full speaker's Count for a half or quarter
   solve.
3. **More** holds **Motion** and **Solve driver in BEAT**. Keep **Normal**
   (each face moves along its own normal) for driver-model solves. **Axial
   (pistonic)** moves the source as a rigid piston and cannot carry a driver
   model. BEAT accepts axial motion only along the radiation axis.
4. Once a driver is chosen, set **Drive voltage** (RMS). The amplifier limit
   is optional and only caps the maximum output Crossover reports.

Without a driver, a channel gives a reference response to **unit
acceleration** (1 m/s² at every frequency). That is useful for response and
directivity, but gives no sensitivity, impedance, power or excursion.

With **Solve driver in BEAT**, enter **Sd (cm²)**: the full physical driver
area, even on half and quarter models. Every enabled sealed rear chamber needs
an entered Sd; it sets the chamber's stiffness. Without Sd, WG works out the
area from the moving faces when it can, and warns when that disagrees with
the datasheet by more than is normal (10–20 % above datasheet Sd is normal,
because the moving faces include the surround). Enter bare **Mmd**, not
datasheet Mms, which includes air load.

**Crossover** sets filters, levels, polarity and delay between drivers.
Check its defaults before trusting a combined response.

**Solver** chooses the engine. **Auto** picks among the engines that can
solve this model. An explicit choice stays yours; WG says why an engine cannot
take the model rather than switching silently.

| Engine | CAD use today |
|---|---|
| **Metal** | Apple GPU. Normal and axial sources, and passive cardioid. |
| **BEMPP** | Needs an OpenCL device, and no free rim away from mirror planes. |
| **BEAT · CPU** | Needs its prepared runtime. No passive cardioid. |
| **BEAT · Metal / CUDA / ROCm** | Needs the matching GPU. CUDA and ROCm are not hardware-qualified; use Accurate with them. No passive cardioid. |

CAD solves are free-space solves. Infinite baffle and a ground plane are not
available for imported models.

## 8. Ports, rear chambers and FEM regions

**Passive cardioid.** Pick the diaphragm as **MF** and the port opening as
**Port or passive opening**. In **Simulation → Passive cardioid**, enable it
and enter rear volume, port length, physical port area and foam resistance.
Use Metal. A hole alone does not tell WG what drives it; an ordinary
bass-reflex box is not recognised automatically.

**FEM regions** solve the air inside chambers, ports and phase plugs and
couple it to the outside field. In Fusion:

1. Model each air volume as one closed solid body.
2. Run **Manage Link… → Declare FEM Region…** on it. Name it, set its mesh
   size and loss factor.
3. Add a group for every face set that is not a plain wall: a **Driver
   diaphragm (moving)** with its role, an **Interface to exterior air** where
   the chamber meets the outside, a **Plane-wave termination** for an anechoic
   tube end, or a **Rigid wall (local size)**, optionally with **Absorbing
   lining**. Ungrouped faces are rigid walls.
4. Hide the air body before Send or Solve.

In WG the regions are listed in Model details, and after a solve WG can show
the pressure inside on a slice plane. Rules worth knowing:

- Declared regions solve on BEAT (CPU float64, or Metal/CUDA/ROCm float32).
- Several moving groups on one role are one physical driver (for example a
  dome and surround, or the front and rear of one diaphragm). To share one
  diaphragm across two regions, give that driver a real driver model in WG.
  Separate drivers need separate roles.
- A plane-wave termination works only in an interior-only solve.
- FEM regions cannot be combined with passive cardioid.
- **Exterior-only (leave FEM regions out)** on the Simulation tab solves
  without the inside air.

## 9. Axis and symmetry

1. WG works out which way a model drawn in Fusion radiates, and never stops
   to ask. **Model details** shows **Radiates along**; when the evidence is
   weak WG says it chose +Z. To pick another axis, click **Change**, choose
   +X, −X, +Y, −Y, +Z or −Z and check the previews. Your choice is kept for
   the project. Save the Fusion document so WG can follow it between sends.
2. Prefer sending the whole speaker. WG checks the geometry and every driver
   for mirror symmetry: one mirror gives a half solve, two a quarter. Keep
   flanges, enclosure, ports and drivers symmetric across every mirror you
   want WG to use.
3. For a model drawn in Fusion, put the throat at the origin and centre the
   speaker on the planes through its axis if you want WG to find symmetry. To
   always solve the whole model, set **Symmetry** to **Off** in Sources &
   Mesh…, Save, and Send again.
4. If you cut the model in Fusion, cut on the YZ or XZ origin plane with the
   model radiating along +Z. Leave the cut open; a flat cap there would be
   solved as a wall. A driver must meet each cut plane.
5. The domain line in Model details may offer **Change** between the readings
   WG found. WG prepares the model again for that choice.

Cuts front to back, off the origin or at an angle are not recovered. Send the
complete model instead.

A real driver model may be cut by a mirror only if its motion axis lies in
that plane. Separate diaphragms, such as a coax or a mirrored pair off the
plane, need separate drivers.

## 10. When WG needs you

**The mesh is too large.** WG's pop-up states the numbers, for example "It
meshes to 54,739 triangles; Waveguide Generator's limit is 22,000." Choose:

- **Solve anyway**: slower and needs more memory.
- **Change in Fusion**: WG cannot open Fusion's dialog for you, so it says
  where to go. For a Solve from Fusion, use **Open Sources & Mesh…** in the
  add-in's progress panel; otherwise use **Waveguide Generator ▸ Sources &
  Mesh…**. Raise **Walls** or add a coarser size zone, Save, and solve again
  from Fusion.
- **Later**: close the pop-up. The status line keeps **Choose…** to reopen it.
- **Don't solve**: let the question go.

**WG could not prepare the model.** When the cause is inside WG, the status
line and Fusion's panel say "Waveguide Generator could not prepare <name>.
This has been recorded for the developers." Use **Try again** when it is
offered. When the cause is something you can change, the line says what to
do, for example send the model again from Fusion.

**A choice about findings.** Some checks need your approval before WG solves.
The model's request shows them with **Approve and solve** or **Dismiss**.
Approving a warning does not repair the geometry.

Requests about other models appear in the top bar ("Another model needs your
choice · Show") and under **Other requests**.

## 11. Read and keep the results

1. Follow progress in WG and open the run. A run belongs to the Fusion
   document and the snapshot that was sent, even if you edit CAD while it
   runs.
2. Look at each driver's response, then **Combined** if you have a
   crossover. Use the directivity maps and polar plots for coverage. Runs with
   a driver model also show impedance, power, current and excursion.
3. Read warnings and failed frequencies. A chart with gaps is not a clean
   sweep. Compare mesh refinements before trusting fine ripple.
4. Export a report, FRD and polar files for VituixCAD, CSV files or the solved
   mesh from the run's **Export** menu.

**Settings → CAD Link → Keep the Fusion model** decides whether WG saves the
Fusion document (`.f3d`) with each version. Versions used by runs are never
deleted automatically. A run's details then show the **Fusion file** with
**Open in Fusion**, which opens a copy as a new document.

Between sends of the same project with the same drivers, WG keeps the driver
setup, crossover and sweep. Check them after changing drivers in Fusion. See
the [run archive](USER-GUIDE.md#the-run-archive) for where runs are stored.

## 12. Messages and what to do

| Message (excerpt) | What to do |
|---|---|
| **Fusion:** "This model has no driver yet." | In Sources & Mesh…, pick a driver's faces, or declare a FEM region with a moving surface. |
| **Fusion:** "…source was split or copied…" | Reopen Sources & Mesh…, pick every face of that driver again, and Save. |
| **Fusion:** "…would carry only part of that source." | Include the hidden or excluded faces, or clear them from the driver. |
| **Fusion:** "…helper body … is … still visible…" | Hide that body itself in Fusion's browser (hiding its folder does not work), or run **Update**. |
| "…would solve as a wall across the cut" | Remove the cap on the cut, or send the whole model. |
| "…no source meets x = 0…" | Send the whole model, or fix the cut and driver geometry. |
| "…its mirror image would stand in for the other side's own source" | Send the whole model when the sides have their own drivers. |
| "Complete the driver, or clear it to solve that channel unit-driven." | Fill in the missing driver values, or clear the driver. |
| "BEAT drives axial sources along its z axis" | That driver's axis is tilted. Use Normal motion, or another engine. |
| "BEMPP solves imported CAD geometry on an OpenCL device only." | Choose an engine that is available, such as Metal or BEAT · CPU. |
| "…infinite baffle is unavailable" / "Turn the ground plane off…" | Use free-space mounting for CAD solves. |
| **Fusion:** "…picked up … but has not confirmed it yet." | Check WG before you send again; sending again starts a new request. |

For the full detail of a Fusion error, open **View → Show Text Commands** in
Fusion.

## 13. STEP from another CAD program

WG has no direct STEP import. To solve a STEP model, open it in Fusion, pick
its drivers with Sources & Mesh…, save the document, and Send or Solve as
above. **Import mesh…** in WG's design menu is a separate route for Gmsh
surface meshes; see [Import and solve a mesh](USER-GUIDE.md#import-and-solve-a-mesh).

Onshape is disabled in release builds. Developers can find its setup in the
[development guide](DEVELOPMENT.md).
