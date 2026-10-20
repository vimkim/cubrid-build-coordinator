# CUBRID build coordination

The stages that produce and install CUBRID artifacts for a selected source worktree.

## Language

**Configure**:
Preparation of the selected CUBRID build rules and settings before producing artifacts.

**Build**:
Production of CUBRID artifacts in the build directory, including compilation and linking.
_Avoid_: Compile as the name of the whole stage, build as a synonym for installation

**Install**:
Placement of existing CUBRID build artifacts into the selected installation directory.

**Build and install**:
The combined operation that produces CUBRID artifacts and then installs them.
_Avoid_: Build when installation is included
