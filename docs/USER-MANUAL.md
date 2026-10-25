# Waveguide Generator: a simple user manual

This manual walks you through Waveguide Generator (WG) one task at a time. Each
task is a short list of steps. For more detail, follow the links to the longer
guides:

- the [user guide](USER-GUIDE.md) covers designs, solvers and results;
- the [CAD Link guide](CAD-LINK-GUIDE.md) covers working with Autodesk Fusion;
- the [install guide](INSTALL.md) covers every install option.

A few words used below:

- **Waveguide**: the horn shape that guides sound from a driver.
- **Solve**: WG's acoustic simulation. It predicts how the design sounds.
- **Mesh**: the small triangles WG divides a surface into before it can solve
  it. More triangles give more detail, but the solve takes longer and needs
  more memory.
- **Driver**: the loudspeaker unit that makes the sound (HF for high
  frequencies, MF for mid, LF for low).

## 1. Install WG and the Fusion add-in

### Install WG

1. Open the [latest release](https://github.com/m3gnus/waveguide-generator/releases/latest)
   and download the file for your computer: `.dmg` for macOS, `-setup.exe`
   for Windows, `.tar.gz` for Linux.
2. Install it:
   - **macOS:** drag the app to Applications and open it. The first time,
     macOS blocks it. Click **Done**, then open **System Settings → Privacy &
     Security** and click **Open Anyway**.
   - **Windows:** run the setup file. If SmartScreen warns you, click **More
     info → Run anyway**. Leave the Fusion add-in task ticked if you use
     Fusion.
   - **Linux:** extract the file and run `./install.sh`.
3. Open WG. The window shows the design on the left, a 3D view in the middle
   and the solve controls at the top right.

The warnings appear because the app is not signed with a paid certificate.
They are not about anything found in the app.

### Install the Fusion add-in

You only need this if you want to work with Autodesk Fusion. Fusion runs on
macOS and Windows.

1. Close Fusion.
2. In WG, click **Imported mesh** at the top, then **CAD Link** on the left.
   If WG shows **Install add-in**, click it. (On Windows, the setup program
   may already have installed it.)
3. In WG, open **Settings** (the gear at the top right). Under **CAD Link**,
   check the **WGLink folder**. This is the folder WG and Fusion share. The
   default is fine for most people.
4. Start Fusion. Open **Utilities → Scripts and Add-Ins → Add-Ins**, select
   **WGLink**, click **Run**, and tick **Run on Startup**.
5. In Fusion, look for the **Waveguide Generator** panel under **Utilities**.
   It holds **Sources & Mesh…**, **Send to WG**, **Solve in WG** and
   **Requests…**.

Keep only one **WGLink** entry in Fusion's add-in list.

## 2. Design a waveguide in WG and solve it

1. At the top of the window, choose **Parametric**.
2. Click the design name at the top left to open the design menu. Choose
   **New** for a fresh design, or **Open…** to open a saved `.cfg` file.
3. On the **Geometry** tab, open **Model Type** and choose a **Family**, for
   example R-OSSE or OSSE.
4. Change the numbers under **Profile**, **Throat**, **Mouth** and
   **Mounting**. The 3D view updates as you type.
5. On the **Simulation** tab, check **Frequency sweep** (the start and end
   frequency, and how many frequencies to solve). Leave **Solver** on its
   automatic choice unless you have a reason to change it.
6. Click **Solve** at the top right.
7. The run appears in the **Jobs** panel with its progress. When it finishes,
   the charts appear in **Results**.

To save your design, click the design name and choose **Export a copy**.
Every solved run also keeps a copy of its design.

## 3. Bring a Fusion model into WG

You can start from a waveguide designed in WG, or from a model you drew in
Fusion.

### Optional: send a WG waveguide to Fusion first

1. In WG, click the design name, then **Export → Send to CAD**.
2. Fusion opens and inserts the waveguide. If it does not, use **Manage
   Link… → Insert** in Fusion's **Waveguide Generator** panel.
3. Save and name the Fusion document. WG uses that name for your runs.
4. Add the baffle, enclosure or other parts around the waveguide. Move the
   whole inserted component, not the bodies inside it.

### Mark the drivers and set the mesh

1. In Fusion, click **Waveguide Generator ▸ Sources & Mesh…**. Its job:
   "Set up sources and mesh, then Send or Solve."
2. The **Drivers** table has one row each for **HF**, **MF**, **LF** and
   **Port or passive opening**. On the row you need, click **Pick faces**, then
   click the driver's faces in the model. A WG waveguide already has its HF
   throat filled in.
3. Leave rows you do not use empty.
4. Set **Walls**: the triangle size for all other surfaces. The default is
   25 mm. A larger number means fewer triangles.
5. Optional: add a **size zone** to make one part finer or coarser than the
   walls. Click **+ Fine size zone (15 mm)** or **+ Coarse size zone
   (40 mm)**, then pick its faces or whole bodies.
6. Read the **Estimate**. It says roughly how many triangles WG will solve,
   and WG's limit. It may show a range, because WG decides later whether it
   can solve only half or a quarter of a symmetric model.
7. Click **Save**. Nothing is sent yet.

### Send or Solve

The panel has two commands. Pick the one you need:

- **Send to WG**: "Open this model in WG to inspect or set up. Does not
  solve." Use it the first time, so you can choose drivers in WG.
- **Solve in WG**: "Send and solve now."

1. Click **Send to WG** or **Solve in WG**.
2. A dialog opens. Leave **Assembly scope** empty to send the whole design,
   or pick one part of it.
3. Read the **Pre-flight** list. It shows the bodies and drivers that will be
   sent, and anything you must fix first. Send and Solve stay greyed out until
   it is fixed.
4. Click **Send** or **Solve**.
5. Fusion's **Waveguide Generator** progress panel opens. It shows each step:
   preparing, solving with a frequency count, then solved. Close it any time;
   **Requests…** opens it again.

If WG is not running, the add-in starts it in the background where it can;
open the WG window to follow along. In WG, the model appears on the
**Imported mesh** tab. WG prepares it by itself; you do not press a
button for that.

### In WG: check the model and solve

1. Look at the **status line** under the model's name. It says what is
   happening and offers at most one button, for example "Ready to solve ·
   3,035 triangles" with **Solve**.
2. Optional: click **Model details** to see the drivers, how the model will
   be solved (whole, half or quarter), which way it radiates, its mesh and the
   checks. Mesh sizes are read-only here. To change them, use **Sources &
   Mesh…** in Fusion and Send again.
3. Open the **Simulation** tab. **Sweep** and **Drivers** are open:
   - In **Sweep**, set the frequency range.
   - In **Drivers**, pick a driver from the library for each driver row, or
     leave it empty. Empty means a simple reference drive with no real driver
     data.
   - **Crossover** (only with two or more drivers), **Directivity** and
     **Solver** are folded. Their one-line summaries show the current choice.
4. Click **Solve** in the status line or at the top right.

## 4. Read the results

1. When a solve finishes, Fusion's progress panel says "Solved" and offers
   **Show results in Waveguide Generator**. In WG, the status line offers
   **Show results**.
2. **Results** shows the charts: on-axis response, horizontal and vertical
   directivity maps, directivity index and more. Use the chart menus to pick a
   different chart.
3. With two or more drivers, choose **Combined** to see the summed response
   with the crossover, or choose one driver to see it alone.
4. Every run is in the **Jobs** panel. For a Fusion model, **View project
   runs** on the **Imported mesh** tab shows only that model's runs.
5. To export, open the run's **Export** menu. It offers a report, FRD and
   polar files for VituixCAD, CSV files and the solved mesh.

Gaps or warnings on a chart mean some frequencies did not solve. Do not judge
a design from those parts. See [Solve, jobs, and results](USER-GUIDE.md#solve-jobs-and-results)
for every chart.

## 5. When WG needs you

Most of the time WG goes from preparing to solved by itself. Two things can
stop it.

### The mesh is too large

WG has a limit on triangles (22,000 for the part it solves). If your model has
more, WG asks before it solves. Fusion's progress panel says WG needs you, and
WG shows a pop-up with the numbers, for example "It meshes to 54,739
triangles; Waveguide Generator's limit is 22,000."

Choose one:

- **Solve anyway**: solve it as it is. It takes longer and needs more memory.
- **Change in Fusion**: WG tells you where to change it. In Fusion, click
  **Open Sources & Mesh…** in the progress panel (or use **Waveguide
  Generator ▸ Sources & Mesh…**), raise **Walls** or add a **Coarse** size
  zone, click **Save**, then Solve again.
- **Later**: close the pop-up. The status line keeps saying the model needs
  your choice, with a **Choose…** button to open the pop-up again.
- **Don't solve**: let this question go without solving.

If you choose **Solve anyway** in Fusion's own pre-flight question, WG does
not ask again.

### WG could not prepare the model

Sometimes WG cannot prepare a model because of a problem inside WG. The status
line then says "Waveguide Generator could not prepare <name>." When WG offers
it, click **Try again**. If it keeps failing, report it (see the FAQ below).

If the status line names something you can fix, such as a missing driver, fix
it in Fusion and Send again.

## 6. FAQ and troubleshooting

**Fusion has no Waveguide Generator panel.**
Open **Utilities → Scripts and Add-Ins**, select **WGLink**, click **Run** and
tick **Run on Startup**. If WGLink is missing, close Fusion and use **Install
add-in** in WG.

**Fusion says WG has not picked up my model.**
Open WG and check that it is running. In WG's **Settings → CAD Link**, check
that the WGLink folder exists. Then click **Send again** in the progress
panel.

**Send and Solve stay greyed out.**
Read the **Pre-flight** list in the dialog. It names what to fix, for example
"This model has no driver yet." Open **Sources & Mesh…** and pick the
driver's faces.

**Where do I change the mesh?**
In Fusion, in **Sources & Mesh…**. WG shows the mesh only for reading.

**I changed the model in Fusion. Does WG see it?**
No. WG keeps a copy of what you sent. Click **Send to WG** or **Solve in WG**
again. Older versions stay under **Model versions** on the **Imported mesh**
tab.

**WG asks nothing about which way my model points. Is that right?**
Yes. WG works it out from the model. To check or change it, open **Model
details** and look for **Radiates along**, then use **Change** if needed.

**Can I see more technical detail?**
Open **Settings**. Under **Advanced**, tick **Show developer details**. It is
off by default and is meant for reporting problems.

**How do I report a problem?**
Use **Report a problem** in the command palette, or the ⓘ button beside
**Settings**. It saves a `.zip` you can attach to an issue. Nothing is sent
for you. See [Reporting a problem](USER-GUIDE.md#reporting-a-problem).
