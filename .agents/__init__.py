# Package marker for the installed distribution.
#
# The source tree keeps the runtime under the hidden ``.agents`` directory so
# the repository can also be used without installing a wheel.  For PEP 517
# builds that directory is mapped to the valid top-level package ``agents``.
# See ``[tool.setuptools.package-dir]`` in pyproject.toml.
