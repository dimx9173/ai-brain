"""Package marker for the tests directory.

We don't need to manipulate `sys.path` here — `ai_brain._testing`
ensures `src/` is on the path lazily, so any test module that does
`import ai_brain` works under any runner.
"""
