/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { AppError } from "@/lib/errors";
import type { HocusPocusServerContext, TDocumentTypes } from "@/types";
// services
import { ProjectPageService } from "./project-page.service";

export const getPageService = (
  documentType: TDocumentTypes,
  context: Pick<HocusPocusServerContext, "documentType" | "workspaceSlug" | "projectId" | "cookie" | "userId">
) => {
  if (documentType === "project_page") {
    return new ProjectPageService({
      workspaceSlug: context.workspaceSlug,
      projectId: context.projectId,
      cookie: context.cookie,
    });
  }

  throw new AppError(`Invalid document type ${documentType} provided.`);
};
