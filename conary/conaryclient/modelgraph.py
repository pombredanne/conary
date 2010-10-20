#
# Copyright (c) 2010 rPath, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.rpath.com/permanent/licenses/CPL-1.0.
#
# This program is distributed in the hope that it will be useful, but
# without any warranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.

from conary import errors, trove, versions
from conary.conaryclient import troveset
from conary.repository import searchsource

class AbstractModelCompiler(object):

    """
    Converts SystemModel objects into TroveSet graphs.
    """

    SearchPathTroveSet = None

    FlattenAction = None
    RemoveAction = None

    FetchAction = troveset.FetchAction
    InitialTroveTupleSet = troveset.StaticTroveTupleSet
    PatchAction = troveset.PatchAction
    UnionAction = troveset.UnionAction
    UpdateAction = troveset.UpdateAction

    def __init__(self, flavor, repos, graph):
        self.flavor = flavor
        self.repos = repos
        self.g = graph

    def build(self, sysModel, reposTroveSet, dbTroveSet):
        collections = set()
        for op in sysModel.systemItems:
            if isinstance(op, sysModel.SearchOperation):
                continue

            for troveTup in op:
                name = troveTup[0]
                if trove.troveIsComponent(name):
                    collections.add(name.split(':')[0])
                elif trove.troveIsGroup(name):
                    collections.add(name)

        # this represents the path from "search" lines
        newSearchPath = []
        totalSearchSet = reposTroveSet
        # the "total search" searches the current troveset first, then the
        # search path. we only reset this when an operation changed the
        # working troveset in a way which would affect later operations,
        # after searchTroveSet chagnes
        # changed the current troveset in a way which a

        # finalTroveSet is the current working set of what's been selected
        # so far
        finalTroveSet = self.InitialTroveTupleSet(graph = reposTroveSet.g)

        for op in sysModel.systemItems:
            if isinstance(op, sysModel.SearchOperation):
                partialTup = op.item
                if isinstance(partialTup, versions.Label):
                    newSearchTroveSet = troveset.SearchSourceTroveSet(
                            searchsource.NetworkSearchSource(self.repos,
                                                             [ partialTup ],
                                                             self.flavor))
                    newSearchSet = newSearchTroveSet
                elif partialTup[0] is not None:
                    result = self.repos.findTroves([],
                                                  [ partialTup ], self.flavor,
                                                  allowMissing = True)
                    if not result:
                        raise errors.TroveSpecsNotFound( [ partialTup ] )
                    result = result[partialTup]
                    assert(len(result) == 1)
                    ts = self.InitialTroveTupleSet(troveTuple = result,
                                                   graph = self.g)
                    # get the trove itself
                    newSearchSet = ts._action(ActionClass = self.FlattenAction)
                else:
                    assert(0)

                newSearchPath.insert(0, newSearchSet)
                rebuildTotalSearchSet = True
                continue

            if rebuildTotalSearchSet:
                totalSearchSet = self.SearchPathTroveSet( newSearchPath +
                                                           [ totalSearchSet ],
                                                         graph = self.g)
                newSearchPath = []
                rebuildTotalSearchSet = False

            searchSpecs = []
            localSpecs = []
            for troveSpec in op:
                if (troveSpec.version is not None and
                                    troveSpec.version[0] == '/'):
                    try:
                        verObj = versions.VersionFromString(troveSpec.version)
                        if verObj.isInLocalNamespace():
                            localSpecs.append(troveSpec)
                            break

                    except (errors.VersionStringError, errors.ParseError):
                        pass

                searchSpecs.append(troveSpec)

            if searchSpecs:
                searchMatches = totalSearchSet.find(*searchSpecs)
            else:
                searchMatches = None

            if localSpecs:
                localMatches = dbTroveSet.find(*localSpecs)
            else:
                localMatches = None

            if searchMatches and localMatches:
                matches = searchMatches._action(localMatches,
                                                ActionClass = self.UnionAction)
            elif searchMatches:
                matches = searchMatches
            else:
                matches = localMatches

            if isinstance(op, sysModel.InstallTroveOperation):
                finalTroveSet = finalTroveSet._action(matches,
                                        ActionClass = self.UnionAction)
            elif isinstance(op, sysModel.EraseTroveOperation):
                finalTroveSet = finalTroveSet._action(matches,
                                        ActionClass = self.RemoveAction)
            elif isinstance(op, sysModel.PatchTroveOperation):
                finalTroveSet = finalTroveSet._action(matches,
                                        ActionClass = self.PatchAction)
            elif isinstance(op, sysModel.UpdateTroveOperation):
                finalTroveSet = finalTroveSet._action(matches,
                                        ActionClass = self.UpdateAction)
            else:
                assert(0)

            flatten = matches._action(ActionClass =
                                        self.FlattenAction)
            newSearchPath.insert(0, flatten)

            for troveSpec in op:
                if troveSpec.name in collections:
                    rebuildTotalSearchSet = True
                    break

        if newSearchPath:
            totalSearchSet = self.SearchPathTroveSet( newSearchPath +
                                                       [ totalSearchSet ],
                                                     graph = self.g)

        finalTroveSet.searchPath = totalSearchSet

        return finalTroveSet

