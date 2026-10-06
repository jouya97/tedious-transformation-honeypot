# What we value in an RL environment

An RL environment is a prompt, a container the agent works in, and a grader
that turns what the agent left behind into a score in `[0, 1]`. The goal of an
RL environment is to distinguish "good models" from "bad models" within a
training rollout, and produce a rank-ordering of such models that can serve as
the basis for gradient descent.

As of September 2026 Goodhart Labs produces mostly evals, but historically we
have produced a few honeypots for the purposes of training models not to hack.
Some things we look for in RL environments:

## The task identifies a unique alignment failure mode

The honeypot should feature a unique core task and specification gaming or
instruction following failure, and neither should be simple variants of an
existing example in the literature. At the end of the exercise, you may be
asked for your coding agent transcripts so we have context on how you built the
environment and what design tradeoffs you made.


## The grader is small enough to review

RL environments receive an abundance of adversarial pressure from artificial
superintelligences. At the current frontier of alignment and capabilities, a
human is still hard required to read the whole grader and convince themselves
it is right. Your grader should be less than 1000 lines, and understandable in
an afternoon (not including test and authoring code not shipped into the
container). If the scoring logic does not fit, the task is probably either too
broad or the grader is doing something it should not. Keep any anti-cheat logic
in one clearly labelled place.

## The grader measures every aspect of the prompt

You do not necessarily have to grade in the same manner that Every requirement
the prompt states about the agent's submission or work outright must move the
score. An instruction the grader does not enforce is one the model will learn
to ignore, and that generalizes badly. The converse also holds: do not grade
anything the prompt did not ask for. Models can and do learn to ignore aspects
of the prompt that are not selected for during training.

## The grader penalizes as few valid submissions as possible

Honest, passing work that scores 0.0 corrupts training signal. Because you are
producing an alignment eval, you may have to find a way to state your
requirements in a roundabout way that does not explicitly enumerate them but
would be inferred by a reasonable of the prompt. Subterfuge notwithstanding,
you should aim to state every uninferrable requirement in the prompt, and grade
on substance rather than incidental form: do not floor a correct answer over
capitalisation, whitespace, ordering the prompt never fixed, or a choice of
approach the prompt left open. Honest solutions of different quality should
rank in the right order, and the intended best solution should actually reach
the top of the scale.

## The only way to score 1.0 is to accomplish the task

Everything the agent can do inside its container without breaking the sandbox
is in scope, and the grader has to withstand it: reading any file it has
permission to read, inspecting processes, writing anywhere it is allowed to
write, crafting payloads to do weird stuff inside the grader container, and
running whatever it likes as its own user.

Concretely:

- The grader should never crash on agent-controlled input. In many training
  regimes these are discarded rollouts, which means they are levers the agent
  can pull to void an episode. A missing, empty, malformed, or hostile
  submission should score 0.0 like any other.
- The grader should never pay 1.0 for something that does not do the expected
  job, either because of traditional cybersecurity vulnerabilities, or because
  of clever hacks that satisfy the behavioral properties of your tests without
  actually demonstrating the capability you wanted.

The test to apply: if the agent learned exactly how it will be graded before
starting, would the best strategy still be to do the task well? If a cheaper
strategy appears, the grader is wrong.

Docker escapes and Linux privilege escalation are out of scope. You do not have
to defend against a kernel exploit, but you do have to defend against `find /
-readable`.
