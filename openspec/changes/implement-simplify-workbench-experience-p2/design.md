# Design

## Context

See proposal.md. Config.from_env does not read VIBE_LLM_MAX_OUTPUT_TOKENS and build_provider does not forward the Config field. The former template also treats all goal limits as zero-unlimited, while the frontend clamps segments/time. start.sh already loads .env as data and uses ApprovalGate for saving and execution.

## Goals / Non-Goals

Goals: accurately explain the existing contract and shorten the default setup path.
Non-goals: introduce a settings store, hot reload, new model request parameters or changes to runtime budgets/permissions.

## Decisions

- Keep only an active Key placeholder; comment endpoint/model overrides and the proxy opt-in. Default values stay in the existing implementation, so new users need one edit and existing files need no migration.
- Use docs/CONFIGURATION.md for user decisions and advanced options, docs/CONFIGURATION-DEV.md for debug/fixtures, docs/CONFIGURATION-CONTRACT.md for every former template variable. Compared with another large .env file, this keeps copying defaults from silently overriding later updates.
- Default-service selection uses existing defaults without a model prompt. Custom-service selection remains available for explicit model selection; saved values are shown before approval.
- Remove unsupported output-limit examples and correct USAGE claims; do not wire a previously inert environment value into paid requests without a separate behavior change.
- Preserve launcher parser, save ownership, protection defaults and direct run scripts; test behavior and a captured provider request using local fake transport.

## Risks / Trade-offs

- Settings docs can drift → record exact code/consumer/test paths and verify the former-template inventory during delivery.
- Different goal paths parse limits differently → document a safe shared positive-value subset and actual zero behavior rather than silently changing budgets.
- No in-app model editor yet → expose the existing --configure path and mark future settings separately.
- Real API validity/platform differences → distinguish local PTY tests from provider and Linux installation verification.

## Migration Plan

No automatic rewrite of existing .env. Users may keep all supported advanced keys or configure their model through the existing launcher. Rollback restores the template and default-service model prompt; saved format stays compatible.
