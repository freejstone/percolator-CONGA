#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Apr  6 23:47:06 2023

@author: jackfreestone

super_percolator main functions
"""
import numpy as np
import pandas as pd
import random
import utility_functions as uf
import logging
import sys
from sklearn import svm
from sklearn.metrics import make_scorer
from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import RandomizedSearchCV
from sklearn.preprocessing import StandardScaler
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.discriminant_analysis import QuadraticDiscriminantAnalysis



def get_bin_freq_pi0(df_all, precursor_bin_width=1.0005079/4):

    #get pi_0, bins, freq
    delta_mass_max = max(abs(df_all.ExpMass - df_all.CalcMass))
    breaks_p = np.arange(0, delta_mass_max + 2*precursor_bin_width,
                         precursor_bin_width) - precursor_bin_width/2
    breaks_n = list(reversed(-breaks_p))
    breaks = pd.Series(
        breaks_n[0:(len(breaks_n) - 1)] + list(breaks_p), name='bins')
    digitized = np.digitize(df_all.ExpMass - df_all.CalcMass, breaks)
    df_all['bins'] = digitized  # binning

    bin_freq = df_all['bins'].value_counts()  # getting bin frequencies
    bin_freq = bin_freq.reset_index()
    bin_freq.columns = ['bins', 'freq']
    df_all = df_all.merge(bin_freq, how='left', on='bins')

    #get pi_0
    pi_0s = df_all.groupby('bins').apply(lambda x: sum(x.Label == 1)/x.freq)
    pi_0s = pi_0s.reset_index()
    pi_0s.drop('level_1', axis=1, inplace=True)
    pi_0s.columns = ['bins', 'pi_0']
    pi_0s.drop_duplicates(inplace=True)
    df_all = df_all.merge(pi_0s, how='left', on='bins')

    return(df_all)
#########################################################################################################



def get_mult(df):
    n = len(df.columns)
    ix = np.indices((n, n))[:, ~np.tri(n, k=-1, dtype=bool)]
    return pd.DataFrame(df.values.T[ix.T].prod(1).T, columns=list(map(tuple, ix.T)))
#########################################################################################################


def peptide_level(df_all, peptide_list_df, precursor_bin_width=1.0005079/4):
    '''
    Parameters
    ----------
    narrow_file : string
        path to filtered narrow pin file.
    open_file : string
        path to filtered open pin file.
    peptide_list : string
        path to target decoy pairing.
    score : string, optional
        Score function for initial direction. The default is 'TailorScore'.

    Returns
    -------
    pandas dataframe of the competed peptides.

    '''

    #select only top 1 narrows or top 2 open psms
    df_all['rank'] = df_all['rank'].astype(int)
    df_all = df_all[((df_all['rank'] <= 2) & (df_all['n_o'] == 0)) | (
        (df_all['rank'] == 1) & (df_all['n_o'] == 1))]
    df_all.loc[df_all['rank'] == 1, 'rank'] = 0
    df_all.loc[df_all['rank'] == 2, 'rank'] = 1

    df_all = df_all.sample(frac=1)  # break ties randomly

    if 'TailorScore' in df_all.columns:
        df_all = df_all.sort_values(by='TailorScore', ascending=False).reset_index(
            drop=True)  # sort by score
    else:
        df_all = df_all.sort_values(by='XCorr', ascending=False).reset_index(
            drop=True)  # sort by score

    # getting best score for each Peptide
    df_all = df_all.drop_duplicates(subset='Peptide')

    df_all_sub = df_all[df_all.Label == -1].copy()
    peptide_list_df.rename(
        columns={'target': 'original_target', 'decoy': 'Peptide'}, inplace=True)
    df_all_sub = df_all_sub.merge(
        peptide_list_df[['original_target', 'Peptide']], how='left', on='Peptide')
    df_all['original_target'] = df_all['Peptide']
    df_all.loc[df_all.Label == -1,
               'original_target'] = df_all_sub['original_target'].tolist()

    df_all['original_target'] = df_all['original_target'].str.replace(
        "\\[|\\]|\\.|\\d+", "", regex=True)

    #adding both target-decoy scores so that they can be trained on as well
    if 'TailorScore' in df_all.columns:
        df_all = df_all.assign(min_tailor_score=df_all.groupby(
            'original_target').TailorScore.transform(lambda x: min(x) if min(x) != max(x) else 0))
    if 'XCorr' in df_all.columns:
        df_all = df_all.assign(min_xcorr_score=df_all.groupby(
            'original_target').XCorr.transform(lambda x: min(x) if min(x) != max(x) else 0))

    df_all = df_all.drop_duplicates(subset='original_target')
    df_all.drop(['original_target', 'enzInt'], axis=1, inplace=True)
    # binning delta masses
    delta_mass_max = max(abs(df_all.ExpMass - df_all.CalcMass))
    breaks_p = np.arange(0, delta_mass_max + 2*precursor_bin_width,
                         precursor_bin_width) - precursor_bin_width/2
    breaks_n = list(reversed(-breaks_p))
    breaks = pd.Series(
        breaks_n[0:(len(breaks_n) - 1)] + list(breaks_p), name='bins')
    digitized = np.digitize(df_all.ExpMass - df_all.CalcMass, breaks)
    df_all['bins'] = digitized

    bin_freq = df_all['bins'].value_counts()  # getting bin frequencies
    bin_freq = bin_freq.reset_index()
    bin_freq.columns = ['bins', 'freq']
    df_all = df_all.merge(bin_freq, how='left', on='bins')

    return(df_all)
#########################################################################################################


def custom_accuracy(y, y_pred, alpha=0.01, p=0.5):
    '''
    Parameters
    ----------
    y : float
        the unobserved target-decoy labels.
    y_pred : float
        the new scores predicted for the unobserved test labels.

    Returns
    -------
    the number of discoveries in y sorted according to y_pred.

    '''
    y = [x for _, x in sorted(zip(y_pred, y), reverse=True)
         ]  # sorting y according to the predicted scores
    decoy_wins = [i == -1 for i in y]
    target_wins = [i == 1 for i in y]
    qvals = uf.TDC_flex_c(decoy_wins, target_wins, BC1=1,
                          c=1 - p/2, lam=1 - p/2)  # doing TDC
    disc = sum((qvals[i] <= alpha) and (y[i] == 1)
               for i in range(len(y)))  # getting discoveries
    return(disc)
#########################################################################################################


def train_cv(labels, df, folds=3, Cs=[0.1, 1, 10], kernel='linear', degree=2, alpha=0.01, p = 0.5):
    '''
    Parameters
    ----------
    labels : panda series
        indicating the target-decoy wins.
    df : panda dataframe
        features to be trained on.
    folds : integer
        number of folds for k-fold cross-validation.
    Cs : list, optional
        the class_weights for SVM. The default is [0.1, 1, 10].
    kernel : string, optional
        the type of SVM kernel. The default is 'linear'.
    degree : integer, optional
        the degree used if polynomial is specified. The default is NULL.
    alpha : float, optional
        FDR threshold. The default is 0.01.

    Returns
    -------
    the optimal choice of parameters based off k-fold cross-validation.

    '''

    if not kernel in ['linear', 'poly', 'rbf']:
        raise ValueError("kernel type not accepted.")

    class_weight = [{-1: C_neg, 1: C_pos}
                    for C_neg in Cs for C_pos in Cs if C_neg >= C_pos]

    if kernel == 'linear':
        param_grid = dict(class_weight=class_weight)
    if kernel == 'rbf':
        gamma_range = np.logspace(-3, 5, 10)
        param_grid = dict(gamma=gamma_range, class_weight=class_weight)
    if kernel == 'poly':
        gamma_range = [0.1, 1, 10]
        coef0_range = [0.1, 1, 10]
        degree = [degree]
        param_grid = dict(gamma=gamma_range, degree=degree, coef0=coef0_range,
                          class_weight=class_weight)

    my_scorer = make_scorer(custom_accuracy, alpha=alpha, p = p,
                            greater_is_better=True, needs_threshold=True)

    if kernel == 'linear':
        grid = GridSearchCV(svm.SVC(), param_grid=param_grid,
                            cv=folds, scoring=my_scorer)
    else:
        grid = GridSearchCV(svm.SVC(), param_grid=param_grid,
                                  cv=folds, scoring=my_scorer)
    grid.fit(df, labels)
    while max(grid.cv_results_['mean_test_score']) == 0:
        alpha = alpha + 0.005
        my_scorer = make_scorer(custom_accuracy, alpha=alpha,
                                greater_is_better=True, needs_threshold=True)

        if kernel == 'linear':
            grid = GridSearchCV(svm.SVC(), param_grid=param_grid,
                                cv=folds, scoring=my_scorer)
        else:
            grid = RandomizedSearchCV(svm.SVC(), param_distributions=param_grid, n_iter=10,
                                      cv=folds, scoring=my_scorer)

        grid.fit(df, labels)

    return(grid)
#########################################################################################################


def train_lda_cv(labels, df):

    clf = LinearDiscriminantAnalysis()
    clf.fit(df, labels)

    return(clf)
#########################################################################################################


def do_iterative_svm_cv(df_all, folds=3, Cs=[0.01, 0.1, 1, 10], total_iter=10, p=0.5, kernel='linear', alpha=0.01, train_alpha=0.01, degree=None, remove=None, top_positive=True):

    #scale non-binary features
    scale = StandardScaler()
    if 'filename' in df_all.columns:
        df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
            df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))])
    else:
        df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
            df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))])

    #create train dataframe
    train_decoy_indxs = random.choices(
        [True, False], k=sum(df_all['Label'] == -1), weights=[1-p, p])
    train_decoys = df_all[df_all['Label'] == -1].copy()
    train_decoys = train_decoys[train_decoy_indxs]

    train_targets = df_all[~(df_all.index.isin(train_decoys.index))].copy()
    train_targets.loc[:, 'Label'] = 1

    train_df = pd.concat([train_decoys, train_targets]).reset_index(drop=True)
    train_df = train_df.sample(frac=1).reset_index(drop=True)
    pi_0s = train_df.groupby('bins').apply(lambda x: sum(x.Label == 1)/x.freq)
    pi_0s = pi_0s.reset_index()
    pi_0s.drop('level_1', axis=1, inplace=True)
    pi_0s.columns = ['bins', 'pi_0']
    pi_0s.drop_duplicates(inplace=True)
    train_df = train_df.merge(pi_0s, how='left', on='bins')
    if type(remove) == list:
        train_df.drop(remove, axis=1, inplace=True)
    if 'TailorScore' in train_df.columns:
        train_df = train_df.sort_values(
            by='TailorScore', ascending=False).reset_index(drop=True)
    else:
        train_df = train_df.sort_values(
            by='XCorr', ascending=False).reset_index(drop=True)

    #real df
    real_df = df_all[~(df_all.index.isin(train_decoys.index))
                     ].copy().reset_index(drop=True)
    real_df = real_df.merge(pi_0s, how='left', on='bins')
    if type(remove) == list:
        real_df.drop(remove, axis=1, inplace=True)

    #Preprocess dataframe
    SVM_train_labels = train_df['Label'].copy()
    if 'filename' in train_df.columns:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
    else:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        
    #Get rid of colinear features
    sds = SVM_train_features.apply(np.std, axis = 0)
    SVM_train_features = SVM_train_features[SVM_train_features.columns[sds != 0]]

    #getting initial positive and negative set
    q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                           SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
    if top_positive:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
    else:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
    negative_set_indxs = (SVM_train_labels == -1)
    
    while sum(positive_set_indxs) == 0:
        train_alpha = train_alpha + 0.005
        #getting initial positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
        negative_set_indxs = (SVM_train_labels == -1)

    SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
        drop=True).copy()
    SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
        drop=True).copy()

    SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
    SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

    SVM_train_features_iter.reset_index(drop=True, inplace=True)
    SVM_train_labels_iter.reset_index(drop=True, inplace=True)

    train_power, train_std, true_power = [
        0]*total_iter, [0]*total_iter, [0]*total_iter

    logging.info("Conducting iterative SVM.")
    sys.stderr.write("Conducting iterative SVM.\n")

    for iterate in range(total_iter):
        logging.info("iteration: %s." % (iterate))
        sys.stderr.write("iteration: %s.\n" % (iterate))
        #determining best direction with cross validation for parameter selection
        grid = train_cv(SVM_train_labels_iter, SVM_train_features_iter,
                        folds=folds, Cs=Cs, kernel=kernel, degree=degree, alpha=train_alpha)
        best_train_power = max(grid.cv_results_['mean_test_score'])
        best_train_std = max(grid.cv_results_['std_test_score'])
        train_power[iterate] = best_train_power
        train_std[iterate] = best_train_std

        #the new direction
        new_scores = grid.decision_function(SVM_train_features)

        new_idx = pd.Series(new_scores).sort_values(ascending=False).index

        SVM_train_features = SVM_train_features.loc[new_idx].reset_index(
            drop=True)

        SVM_train_labels = SVM_train_labels.loc[new_idx].reset_index(drop=True)

        #determine the new positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
        
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha)
        negative_set_indxs = (SVM_train_labels == -1)
        
        while sum(positive_set_indxs) == 0:
            train_alpha = train_alpha + 0.005
            #getting initial positive and negative set
            q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                                   SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
            if top_positive:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
            else:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
            negative_set_indxs = (SVM_train_labels == -1)

        SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
            drop=True).copy()
        SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
            drop=True).copy()

        SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
        SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

        SVM_train_features_iter.reset_index(drop=True, inplace=True)
        SVM_train_labels_iter.reset_index(drop=True, inplace=True)

        #get actual power if we were to stop here
        real_labels = real_df['Label'].copy()
        if 'filename' in real_df.columns:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        else:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
            
        #Get rid of colinear features
        sds = real_df_test.apply(np.std, axis = 0)
        real_df_test = real_df_test[real_df_test.columns[sds != 0]]

        #the new direction
        new_scores = grid.decision_function(real_df_test)
        new_idx = pd.Series(new_scores).sort_values(ascending=False).index
        new_labels = real_labels.loc[new_idx].reset_index(drop=True)

        q_val = uf.TDC_flex_c(
            new_labels == -1, new_labels == 1, c=1/(1 + p), lam=1/(1 + p))
        power_final = sum((q_val <= alpha) & (new_labels == 1))
        true_power[iterate] = power_final

        logging.info("Observed power: %s." % (power_final))
        sys.stderr.write("Observed power: %s.\n" % (power_final))

        logging.info("Trained power: %s." % (best_train_power))
        sys.stderr.write("Trained power: %s.\n" % (best_train_power))

        logging.info("Std trained power: %s." % (best_train_std))
        sys.stderr.write("Std trained power: %s.\n" % (best_train_std))

    #using the last new_idx to report the discoveries
    real_df = real_df.loc[new_idx].reset_index(drop=True)
    real_df_discoveries = real_df[(q_val <= alpha) & (new_labels == 1)]

    return(train_power, train_std, true_power, real_df_discoveries)
#########################################################################################################

def do_iterative_stratified_svm_cv(df_all, folds=3, Cs=[0.01, 0.1, 1, 10], FDR_lattice = [0.2, 0.1, 0.05], total_iter=10, p = 0.5, kernel='linear', alpha=0.01, train_alpha=0.01, degree=None, remove=None, top_positive = True):
    
    #scale non-binary features
    scale = StandardScaler()
    if 'filename' in df_all.columns:
        df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
            df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))])
    else:
        df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
            df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))])
        
    #create train dataframe
    train_decoy_indxs = random.choices(
        [True, False], k=sum(df_all['Label'] == -1), weights = [1-p, p])
    train_decoys = df_all[df_all['Label'] == -1].copy()
    train_decoys = train_decoys[train_decoy_indxs]

    train_targets = df_all[~(df_all.index.isin(train_decoys.index))].copy()
    train_targets.loc[:, 'Label'] = 1

    train_df = pd.concat([train_decoys, train_targets]).reset_index(drop=True)
    train_df = train_df.sample(frac=1).reset_index(drop=True)
    pi_0s = train_df.groupby('bins').apply(lambda x: sum(x.Label == 1)/x.freq)
    pi_0s = pi_0s.reset_index()
    pi_0s.drop('level_1', axis=1, inplace=True)
    pi_0s.columns = ['bins', 'pi_0']
    pi_0s.drop_duplicates(inplace=True)
    train_df = train_df.merge(pi_0s, how='left', on='bins')
    if type(remove) == list:
        train_df.drop(remove, axis=1, inplace=True)
    if 'TailorScore' in train_df.columns:
        train_df = train_df.sort_values(
            by='TailorScore', ascending=False).reset_index(drop=True)
    else:
        train_df = train_df.sort_values(
            by='XCorr', ascending=False).reset_index(drop=True)

    #real df
    real_df = df_all[~(df_all.index.isin(train_decoys.index))
                     ].copy().reset_index(drop=True)
    real_df = real_df.merge(pi_0s, how='left', on='bins')
    if type(remove) == list:
        real_df.drop(remove, axis=1, inplace=True)

    #Preprocess dataframe
    SVM_train_labels = train_df['Label'].copy()
    if 'filename' in train_df.columns:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
    else:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        
    #Get rid of colinear features
    sds = SVM_train_features.apply(np.std, axis=0)
    SVM_train_features = SVM_train_features[SVM_train_features.columns[sds != 0]]

    #getting initial positive and negative set
    q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                           SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
    if top_positive:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
            SVM_train_features['rank'] == min(SVM_train_features['rank']))
    else:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
    negative_set_indxs = (SVM_train_labels == -1)

    while sum(positive_set_indxs) == 0:
        train_alpha = train_alpha + 0.005
        #getting initial positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
                SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (
                q_vals <= train_alpha)
        negative_set_indxs = (SVM_train_labels == -1)

    SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
        drop=True).copy()
    SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
        drop=True).copy()

    SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
    SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

    SVM_train_features_iter.reset_index(drop=True, inplace=True)
    SVM_train_labels_iter.reset_index(drop=True, inplace=True)

    train_power, train_std, true_power = [
        0]*total_iter, [0]*total_iter, [0]*total_iter

    logging.info("Conducting iterative SVM.")
    sys.stderr.write("Conducting iterative SVM.\n")

    for iterate in range(total_iter):
        logging.info("iteration: %s." % (iterate))
        sys.stderr.write("iteration: %s.\n" % (iterate))
        #determining best direction with cross validation for parameter selection
        grid = train_cv(SVM_train_labels_iter, SVM_train_features_iter,
                        folds=folds, Cs=Cs, kernel=kernel, degree=degree, alpha=train_alpha)
        best_train_power = max(grid.cv_results_['mean_test_score'])
        best_train_std = max(grid.cv_results_['std_test_score'])
        train_power[iterate] = best_train_power
        train_std[iterate] = best_train_std

        #the new direction
        new_scores = grid.decision_function(SVM_train_features)

        new_idx = pd.Series(new_scores).sort_values(ascending=False).index

        SVM_train_features = SVM_train_features.loc[new_idx].reset_index(
            drop=True)

        SVM_train_labels = SVM_train_labels.loc[new_idx].reset_index(drop=True)

        #determine the new positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)

        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha) & (
                SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha)
        negative_set_indxs = (SVM_train_labels == -1)

        while sum(positive_set_indxs) == 0:
            train_alpha = train_alpha + 0.005
            #getting initial positive and negative set
            q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                                   SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
            if top_positive:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
                    SVM_train_features['rank'] == min(SVM_train_features['rank']))
            else:
                positive_set_indxs = (SVM_train_labels == 1) & (
                    q_vals <= train_alpha)
            negative_set_indxs = (SVM_train_labels == -1)

        SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
            drop=True).copy()
        SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
            drop=True).copy()

        SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
        SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

        SVM_train_features_iter.reset_index(drop=True, inplace=True)
        SVM_train_labels_iter.reset_index(drop=True, inplace=True)

        #get actual power if we were to stop here
        real_labels = real_df['Label'].copy()
        if 'filename' in real_df.columns:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins', 'n_o'], axis=1).copy()
        else:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins', 'n_o'], axis=1).copy()

        #Get rid of colinear features
        sds = real_df_test.apply(np.std, axis=0)
        real_df_test = real_df_test[real_df_test.columns[sds != 0]]

        #the new direction
        new_scores = grid.decision_function(real_df_test)
        new_idx = pd.Series(new_scores).sort_values(ascending=False).index
        new_labels = real_labels.loc[new_idx].reset_index(drop=True)

        q_val = uf.TDC_flex_c(
            new_labels == -1, new_labels == 1, c=1/(1 + p), lam=1/(1 + p))
        power_final = sum((q_val <= alpha) & (new_labels == 1))
        true_power[iterate] = power_final

        logging.info("Observed power: %s." % (power_final))
        sys.stderr.write("Observed power: %s.\n" % (power_final))

        logging.info("Trained power: %s." % (best_train_power))
        sys.stderr.write("Trained power: %s.\n" % (best_train_power))

        logging.info("Std trained power: %s." % (best_train_std))
        sys.stderr.write("Std trained power: %s.\n" % (best_train_std))

    #using the last new_idx to report the discoveries
    real_df = real_df.loc[new_idx].reset_index(drop=True)
    real_df_discoveries = real_df[(q_val <= alpha) & (new_labels == 1)]

    return(train_power, train_std, true_power, real_df_discoveries)
#########################################################################################################


def do_iterative_lda_cv(df_all, total_iter=10, p=0.5, alpha=0.01, train_alpha=0.01, remove=None, top_positive=True, qda=False):

    if qda:
        df_all_new = get_mult(df_all.loc[:, ~(df_all.columns.isin(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))])
        df_all_new.columns = range(len(df_all_new.columns))
        df_all_new.columns = df_all_new.columns.astype(str)
        df_all = pd.concat([df_all, df_all_new], axis=1)

    #scale non-binary features
    scale = StandardScaler()
    if 'filename' in df_all.columns:
        df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
            df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))])
    else:
        df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
            df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))])

    #create train dataframe
    train_decoy_indxs = random.choices(
        [True, False], k=sum(df_all['Label'] == -1), weights=[1-p, p])
    train_decoys = df_all[df_all['Label'] == -1].copy()
    train_decoys = train_decoys[train_decoy_indxs]

    train_targets = df_all[~(df_all.index.isin(train_decoys.index))].copy()
    train_targets.loc[:, 'Label'] = 1

    train_df = pd.concat([train_decoys, train_targets]).reset_index(drop=True)
    train_df = train_df.sample(frac=1).reset_index(drop=True)
    pi_0s = train_df.groupby('bins').apply(lambda x: sum(x.Label == 1)/x.freq)
    pi_0s = pi_0s.reset_index()
    pi_0s.drop('level_1', axis=1, inplace=True)
    pi_0s.columns = ['bins', 'pi_0']
    pi_0s.drop_duplicates(inplace=True)
    train_df = train_df.merge(pi_0s, how='left', on='bins')
    if type(remove) == list:
        train_df.drop(remove, axis=1, inplace=True)
    if 'TailorScore' in train_df.columns:
        train_df = train_df.sort_values(
            by='TailorScore', ascending=False).reset_index(drop=True)
    else:
        train_df = train_df.sort_values(
            by='XCorr', ascending=False).reset_index(drop=True)

    #real df
    real_df = df_all[~(df_all.index.isin(train_decoys.index))
                     ].copy().reset_index(drop=True)
    real_df = real_df.merge(pi_0s, how='left', on='bins')
    if type(remove) == list:
        real_df.drop(remove, axis=1, inplace=True)

    #Preprocess dataframe
    SVM_train_labels = train_df['Label'].copy()
    if 'filename' in train_df.columns:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
    else:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()

    #Get rid of colinear features
    sds = SVM_train_features.apply(np.std, axis=0)
    SVM_train_features = SVM_train_features[SVM_train_features.columns[sds != 0]]

    #getting initial positive and negative set
    q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                           SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
    if top_positive:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
            SVM_train_features['rank'] == min(SVM_train_features['rank']))
    else:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
    negative_set_indxs = (SVM_train_labels == -1)

    while sum(positive_set_indxs) == 0:
        train_alpha = train_alpha + 0.005
        #getting initial positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
                SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (
                q_vals <= train_alpha)
        negative_set_indxs = (SVM_train_labels == -1)

    SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
        drop=True).copy()
    SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
        drop=True).copy()

    SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
    SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

    SVM_train_features_iter.reset_index(drop=True, inplace=True)
    SVM_train_labels_iter.reset_index(drop=True, inplace=True)

    true_power = [0]*total_iter

    logging.info("Conducting iterative SVM.")
    sys.stderr.write("Conducting iterative SVM.\n")

    for iterate in range(total_iter):
        logging.info("iteration: %s." % (iterate))
        sys.stderr.write("iteration: %s.\n" % (iterate))
        #determining best direction with cross validation for parameter selection
        grid = train_lda_cv(SVM_train_labels_iter, SVM_train_features_iter)

        #the new direction
        new_scores = grid.decision_function(SVM_train_features)

        new_idx = pd.Series(new_scores).sort_values(ascending=False).index

        SVM_train_features = SVM_train_features.loc[new_idx].reset_index(
            drop=True)

        SVM_train_labels = SVM_train_labels.loc[new_idx].reset_index(drop=True)

        #determine the new positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)

        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha) & (
                SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha)
        negative_set_indxs = (SVM_train_labels == -1)

        while sum(positive_set_indxs) == 0:
            train_alpha = train_alpha + 0.005
            #getting initial positive and negative set
            q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                                   SVM_train_labels == 1, c=(p + 1)/2, lam=(p + 1)/2)
            if top_positive:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
                    SVM_train_features['rank'] == min(SVM_train_features['rank']))
            else:
                positive_set_indxs = (SVM_train_labels == 1) & (
                    q_vals <= train_alpha)
            negative_set_indxs = (SVM_train_labels == -1)

        SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
            drop=True).copy()
        SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
            drop=True).copy()

        SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
        SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

        SVM_train_features_iter.reset_index(drop=True, inplace=True)
        SVM_train_labels_iter.reset_index(drop=True, inplace=True)

        #get actual power if we were to stop here
        real_labels = real_df['Label'].copy()
        if 'filename' in real_df.columns:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        else:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()

        #Get rid of colinear features
        sds = real_df_test.apply(np.std, axis=0)
        real_df_test = real_df_test[real_df_test.columns[sds != 0]]

        #the new direction
        new_scores = grid.decision_function(real_df_test)
        new_idx = pd.Series(new_scores).sort_values(ascending=False).index
        new_labels = real_labels.loc[new_idx].reset_index(drop=True)

        q_val = uf.TDC_flex_c(
            new_labels == -1, new_labels == 1, c=1/(1 + p), lam=1/(1 + p))
        power_final = sum((q_val <= alpha) & (new_labels == 1))
        true_power[iterate] = power_final

        logging.info("Observed power: %s." % (power_final))
        sys.stderr.write("Observed power: %s.\n" % (power_final))

    #using the last new_idx to report the discoveries
    real_df = real_df.loc[new_idx].reset_index(drop=True)
    real_df_discoveries = real_df[(q_val <= alpha) & (new_labels == 1)]

    return(true_power, real_df_discoveries)


#########################################################################################################


def do_scale(df_all):
    #scale non-binary features
    scale = StandardScaler()
    if 'filename' in df_all.columns:
        df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
            df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))])
    else:
        df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
            df_all.loc[:, ~(df_all.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))])

    return(df_all)

#########################################################################################################


def do_svm_single_iterate(grid, SVM_train_labels, SVM_train_features, SVM_train_labels_iter, SVM_train_features_iter, train_alpha, prop, top_positive):

    best_train_power = max(grid.cv_results_['mean_test_score'])
    best_train_std = max(grid.cv_results_['std_test_score'])

    #the new direction
    new_scores = grid.decision_function(SVM_train_features)

    new_idx = pd.Series(new_scores).sort_values(ascending=False).index

    SVM_train_features = SVM_train_features.loc[new_idx].reset_index(
        drop=True)

    SVM_train_labels = SVM_train_labels.loc[new_idx].reset_index(drop=True)

    #determine the new positive and negative set
    q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                           SVM_train_labels == 1, c=(1 - prop/2), lam=(1 - prop/2))

    if top_positive:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
            SVM_train_features['rank'] == min(SVM_train_features['rank']))
    else:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
    negative_set_indxs = (SVM_train_labels == -1)

    while sum(positive_set_indxs) == 0:
        train_alpha = train_alpha + 0.005
        #getting initial positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(1 - prop/2), lam=(1 - prop/2))
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
                SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (
                q_vals <= train_alpha)        
    
    negative_set_indxs_above = (SVM_train_labels == -1) & (q_vals <= train_alpha)

    SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
        drop=True).copy()
    SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
        drop=True).copy()
    SVM_train_features_negative = SVM_train_features.loc[negative_set_indxs_above, :].reset_index(
        drop=True).copy()

    SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
    SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

    SVM_train_features_iter.reset_index(drop=True, inplace=True)
    SVM_train_labels_iter.reset_index(drop=True, inplace=True)

    return(best_train_power, best_train_std, SVM_train_labels_iter, SVM_train_features_iter, SVM_train_features_negative)


#########################################################################################################


def do_iterative_svm_cv_stratified(df_all, folds=3, Cs=[0.1, 1, 10], p=1/2, total_iter=3, FDR_lattice=[1, 0.1], kernel='linear', alpha=0.01, train_alpha=0.01, degree=None, remove=None, top_positive=True):

    df_all = do_scale(df_all)  # scale

    #use initial value in FDR_lattice
    df_all_temp = df_all.sample(frac=1).copy()
    df_all_temp = df_all_temp.sort_values(
        by='TailorScore', ascending=False).reset_index(drop=True)
    q_vals = uf.TDC_flex_c(df_all_temp.Label == -1,
                           df_all_temp.Label == 1, c=1/2, lam=1/2)

    #get it down to the top FDR_lattice[0]%
    df_all_temp = df_all_temp[(q_vals <= FDR_lattice[0])
                              ].reset_index(drop=True)

    #getting initial train
    n_lattice = len(FDR_lattice[1:]) + 1
    # need to handle if not exactly 1/(2*n_lattice) is selected
    train_decoys = df_all_temp[df_all_temp['Label']
                               == -1].sample(frac=p/n_lattice).copy()
    train_targets = df_all_temp[~(
        df_all_temp.index.isin(train_decoys.index))].copy()
    train_targets.loc[:, 'Label'] = 1

    #create train dataframe
    train_df = pd.concat([train_decoys, train_targets]).reset_index(drop=True)
    train_df = train_df.sample(frac=1).reset_index(drop=True)
    pi_0s = train_df.groupby('bins').apply(lambda x: sum(x.Label == 1)/x.freq)
    pi_0s = pi_0s.reset_index()
    pi_0s.drop('level_1', axis=1, inplace=True)
    pi_0s.columns = ['bins', 'pi_0']
    pi_0s.drop_duplicates(inplace=True)
    train_df = train_df.merge(pi_0s, how='left', on='bins')
    if type(remove) == list:
        train_df.drop(remove, axis=1, inplace=True)
    if 'TailorScore' in train_df.columns:
        train_df = train_df.sort_values(
            by='TailorScore', ascending=False).reset_index(drop=True)
    else:
        train_df = train_df.sort_values(
            by='XCorr', ascending=False).reset_index(drop=True)

    #real df
    real_df = df_all_temp[~(df_all_temp.index.isin(train_decoys.index))
                          ].copy().reset_index(drop=True)
    real_df = real_df.merge(pi_0s, how='left', on='bins')
    if type(remove) == list:
        train_decoys.drop(remove, axis = 1, inplace = True, errors = 'ignore')
        real_df.drop(remove, axis=1, inplace=True)

    #Preprocess dataframe
    SVM_train_labels = train_df['Label'].copy()
    if 'filename' in train_df.columns:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
    else:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()

    #Get rid of colinear features
    sds = SVM_train_features.apply(np.std, axis=0)
    SVM_train_features = SVM_train_features[SVM_train_features.columns[sds != 0]]

    #getting initial positive and negative set
    q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                           SVM_train_labels == 1, c=(1 - p/(2*n_lattice)), lam=(1 - p/(2*n_lattice)))
    if top_positive:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
            SVM_train_features['rank'] == min(SVM_train_features['rank']))
    else:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
    negative_set_indxs = (SVM_train_labels == -1)

    while sum(positive_set_indxs) == 0:
        train_alpha = train_alpha + 0.005
        #getting initial positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(1 - p/(2*n_lattice)), lam=(1 - p/(2*n_lattice)))
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
                SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (
                q_vals <= train_alpha)
        negative_set_indxs = (SVM_train_labels == -1)

    SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
        drop=True).copy()
    SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
        drop=True).copy()

    SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
    SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

    SVM_train_features_iter.reset_index(drop=True, inplace=True)
    SVM_train_labels_iter.reset_index(drop=True, inplace=True)

    train_power, train_std, true_power = [], [], []

    logging.info("Conducting iterative SVM.")
    sys.stderr.write("Conducting iterative SVM.\n")

    i = 1
    while i <= n_lattice:

        for iterate in range(total_iter):
            logging.info("iteration: %s." % (iterate))
            sys.stderr.write("iteration: %s.\n" % (iterate))

            #determining best direction with cross validation for parameter selection
            grid = train_cv(SVM_train_labels_iter, SVM_train_features_iter,
                            folds=folds, Cs=Cs, kernel=kernel, degree=degree, alpha=train_alpha)

            #then get new positive and negative training set
            tr_pw, tr_std, SVM_train_labels_iter, SVM_train_features_iter, SVM_train_features_negative = do_svm_single_iterate(
                grid, SVM_train_labels, SVM_train_features, SVM_train_labels_iter, SVM_train_features_iter, train_alpha, p*i/(n_lattice), top_positive)

            train_power.append(tr_pw)
            train_std.append(tr_std)

            #get actual power if we were to stop here
            real_labels = real_df['Label'].copy()
            if 'filename' in real_df.columns:
                real_df_test = real_df.drop(
                    ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
            else:
                real_df_test = real_df.drop(
                    ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()

            #Get rid of colinear features
            sds = real_df_test.apply(np.std, axis=0)
            real_df_test = real_df_test[real_df_test.columns[sds != 0]]

            #the new direction
            new_scores = grid.decision_function(real_df_test)
            new_idx = pd.Series(new_scores).sort_values(ascending=False).index
            new_labels = real_labels.loc[new_idx].reset_index(drop=True)

            q_val = uf.TDC_flex_c(
                new_labels == -1, new_labels == 1, c=1/(2 - p*i/n_lattice), lam=1/(2 - p*i/n_lattice))
            power_final = sum((q_val <= alpha) & (new_labels == 1))
            true_power.append(power_final)

            logging.info("Observed power: %s." % (power_final))
            sys.stderr.write("Observed power: %s.\n" % (power_final))

            logging.info("Trained power: %s." % (train_power[-1]))
            sys.stderr.write("Trained power: %s.\n" % (train_power[-1]))

            logging.info("Std trained power: %s." % (train_std[-1]))
            sys.stderr.write("Std trained power: %s.\n" % (train_std[-1]))

        if i == n_lattice:
            break

        ###############################################################################################

        #iterate
        i += 1
        
        #try adding the svm score to help "stabilize" things

        #eat into the peptides until estimated FDR is less than or equal to the next FDR_lattice values
        real_df['SVM_score'] = new_scores
        real_df = real_df.sort_values(by='SVM_score', ascending=False).reset_index(drop = True)
        real_df_temp = real_df[(q_val > FDR_lattice[i - 1])].reset_index(drop=True)
        real_df = real_df[(q_val <= FDR_lattice[i - 1])].reset_index(drop=True)
        
    
        #unsure if scaling is really valuable here....
        #scale = StandardScaler()
        #if 'filename' in real_df.columns:
        #    real_df.loc[:, ~(real_df.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
        #        real_df.loc[:, ~(real_df.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))])
        #else:
        #    real_df.loc[:, ~(real_df.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
        #        real_df.loc[:, ~(real_df.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))])

        
        #get previous decoys above threshold
        #new_scores[::-1].sort()
        #new_scores = pd.Series(new_scores)
        min_score = min(real_df.SVM_score)
        decoy_new_scores = grid.decision_function(SVM_train_features[SVM_train_labels == -1])
        train_decoys_temp = SVM_train_features[SVM_train_labels == -1].copy()
        train_decoys_temp['SVM_score'] = decoy_new_scores

        # select a new set of decoys from the remaining peptides
        train_decoys_new = real_df[real_df['Label']
                               == -1].sample(frac=p/n_lattice).copy()
        
        #update real_df
        train_targets = real_df[~(
            real_df.index.isin(train_decoys_new.index))].copy()
        
        real_df = real_df[~(real_df.index.isin(train_decoys_new.index))
                              ].copy().reset_index(drop=True)
        
        #old decoys can be added in
        #train_decoys = pd.concat([train_decoys_new, train_decoys_temp])
        train_decoys = train_decoys_new
        
        #get train_df
        train_targets.loc[:, 'Label'] = 1
        train_df = pd.concat([train_decoys, train_targets]
                             ).reset_index(drop=True)
        
        train_df = pd.concat([train_df, real_df_temp]) #and previously observed targets/decoys that we went over

        train_df = train_df.sample(frac=1).reset_index(drop=True)
        
        scale = StandardScaler()
        if 'filename' in real_df.columns:
            train_df.loc[:, ~(train_df.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
                train_df.loc[:, ~(train_df.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))])
            real_df.loc[:, ~(real_df.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))] = scale.transform(
                real_df.loc[:, ~(real_df.columns.isin(['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins']))])
        else:
            train_df.loc[:, ~(train_df.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))] = scale.fit_transform(
                train_df.loc[:, ~(train_df.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))])
            real_df.loc[:, ~(real_df.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))] = scale.transform(
                real_df.loc[:, ~(real_df.columns.isin(['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins']))])

    
        #Preprocess dataframe
        SVM_train_labels = train_df['Label'].copy()
        if 'filename' in train_df.columns:
            SVM_train_features = train_df.drop(
                ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        else:
            SVM_train_features = train_df.drop(
                ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()

        #Get rid of colinear features
        sds = SVM_train_features.apply(np.std, axis=0)
        SVM_train_features = SVM_train_features[SVM_train_features.columns[sds != 0]]
        
        SVM_train_features = SVM_train_features.sort_values(by = 'SVM_score', ascending = False)

        new_idx = SVM_train_features.index
        
        SVM_train_labels = SVM_train_labels.loc[new_idx].reset_index(drop=True)
        
        #SVM_train_features = SVM_train_features.sort_values(by='TailorScore', ascending=False)
        
        #SVM_train_labels = SVM_train_labels.loc[SVM_train_features.index].reset_index(drop=True)
        
        SVM_train_features = SVM_train_features.reset_index(drop = True)

        #getting initial positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=(1 - p*i/(2*n_lattice)), lam=(1 - p*i/(2*n_lattice)))
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
                SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (
                q_vals <= train_alpha)

        while sum(positive_set_indxs) == 0:
            train_alpha = train_alpha + 0.005
            #getting initial positive and negative set
            q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                                   SVM_train_labels == 1, c=(1 - p*i/(2*n_lattice)), lam=(1 - p*i/(2*n_lattice)))
            if top_positive:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (
                    SVM_train_features['rank'] == min(SVM_train_features['rank']))
            else:
                positive_set_indxs = (SVM_train_labels == 1) & (
                    q_vals <= train_alpha)
        negative_set_indxs = (SVM_train_labels == -1)

        SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
            drop=True).copy()
        SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
            drop=True).copy()

        SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
        SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

        SVM_train_features_iter.reset_index(drop=True, inplace=True)
        SVM_train_labels_iter.reset_index(drop=True, inplace=True)

    #using the last new_idx to report the discoveries
    real_df = real_df.loc[new_idx].reset_index(drop=True)
    real_df_discoveries = real_df[(q_val <= alpha) & (new_labels == 1)]

    return(train_power, train_std, true_power, real_df_discoveries)




################################################################################################

def do_svm(df_all, train_decoys, folds=3, Cs=[0.01, 0.1, 1, 10], total_iter=10, p=0.5, kernel='linear', alpha=0.01, train_alpha=0.01, degree=None, remove=None, top_positive=True):

    train_targets = df_all[~(df_all.index.isin(train_decoys.index))].copy()
    train_targets.loc[:, 'Label'] = 1

    train_df = pd.concat([train_decoys, train_targets]).reset_index(drop=True)
    train_df = train_df.sample(frac=1).reset_index(drop=True)
    
    if type(remove) == list:
        train_df.drop(remove, axis=1, inplace=True, errors = 'ignore')
    if 'SVM_score' in train_df.columns:
        train_df = train_df.sort_values(
            by = 'SVM_score', ascending=False).reset_index(drop = True)
    elif 'TailorScore' in train_df.columns:
        train_df = train_df.sort_values(
            by='TailorScore', ascending=False).reset_index(drop=True)
    else:
        train_df = train_df.sort_values(
            by='XCorr', ascending=False).reset_index(drop=True)

    #real df
    real_df = df_all[~(df_all.index.isin(train_decoys.index))
                     ].copy().reset_index(drop=True)
    if type(remove) == list:
        real_df.drop(remove, axis=1, inplace=True, errors = 'ignore')

    #Preprocess dataframe
    SVM_train_labels = train_df['Label'].copy()
    if 'filename' in train_df.columns:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
    else:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        
    #Get rid of colinear features
    sds = SVM_train_features.apply(np.std, axis = 0)
    SVM_train_features = SVM_train_features[SVM_train_features.columns[sds != 0]]

    #getting initial positive and negative set
    q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                           SVM_train_labels == 1, c=1 - p/2, lam=1 - p/2)
    if top_positive:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
    else:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
    negative_set_indxs = (SVM_train_labels == -1)
    
    while sum(positive_set_indxs) == 0:
        train_alpha = train_alpha + 0.005
        #getting initial positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=1 - p/2, lam=1 - p/2)
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
        negative_set_indxs = (SVM_train_labels == -1)

    SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
        drop=True).copy()
    SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
        drop=True).copy()

    SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
    SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

    SVM_train_features_iter.reset_index(drop=True, inplace=True)
    SVM_train_labels_iter.reset_index(drop=True, inplace=True)

    train_power, train_std, true_power = [
        0]*total_iter, [0]*total_iter, [0]*total_iter

    logging.info("Conducting iterative SVM.")
    sys.stderr.write("Conducting iterative SVM.\n")

    for iterate in range(total_iter):
        logging.info("iteration: %s." % (iterate))
        sys.stderr.write("iteration: %s.\n" % (iterate))
        #determining best direction with cross validation for parameter selection
        grid = train_cv(SVM_train_labels_iter, SVM_train_features_iter,
                        folds=folds, Cs=Cs, kernel=kernel, degree=degree, alpha=train_alpha, p = p)
        best_train_power = max(grid.cv_results_['mean_test_score'])
        best_train_std = max(grid.cv_results_['std_test_score'])
        train_power[iterate] = best_train_power
        train_std[iterate] = best_train_std

        #the new direction
        new_scores = grid.decision_function(SVM_train_features)

        new_idx = pd.Series(new_scores).sort_values(ascending=False).index

        SVM_train_features = SVM_train_features.loc[new_idx].reset_index(
            drop=True)

        SVM_train_labels = SVM_train_labels.loc[new_idx].reset_index(drop=True)

        #determine the new positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=1 - p/2, lam=1 - p/2)
        
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha)
        negative_set_indxs = (SVM_train_labels == -1)
        
        while sum(positive_set_indxs) == 0:
            train_alpha = train_alpha + 0.005
            #getting initial positive and negative set
            q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                                   SVM_train_labels == 1, c=1 - p/2, lam=1 - p/2)
            if top_positive:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
            else:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
            negative_set_indxs = (SVM_train_labels == -1)

        SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
            drop=True).copy()
        SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
            drop=True).copy()

        SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
        SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

        SVM_train_features_iter.reset_index(drop=True, inplace=True)
        SVM_train_labels_iter.reset_index(drop=True, inplace=True)

        #get actual power if we were to stop here
        real_labels = real_df['Label'].copy()
        if 'filename' in real_df.columns:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        else:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
            
        #Get rid of colinear features
        sds = real_df_test.apply(np.std, axis = 0)
        real_df_test = real_df_test[real_df_test.columns[sds != 0]]

        #the new direction
        new_scores = grid.decision_function(real_df_test)
        new_idx = pd.Series(new_scores).sort_values(ascending=False).index
        new_labels = real_labels.loc[new_idx].reset_index(drop=True)

        q_val = uf.TDC_flex_c(
            new_labels == -1, new_labels == 1, c=1/(2 - p), lam=1/(2 - p))
        power_final = sum((q_val <= alpha) & (new_labels == 1))
        true_power[iterate] = power_final

        logging.info("Observed power: %s." % (power_final))
        sys.stderr.write("Observed power: %s.\n" % (power_final))

        logging.info("Trained power: %s." % (best_train_power))
        sys.stderr.write("Trained power: %s.\n" % (best_train_power))

        logging.info("Std trained power: %s." % (best_train_std))
        sys.stderr.write("Std trained power: %s.\n" % (best_train_std))

    #using the last new_idx to report the discoveries
    real_df['SVM_score'] = new_scores
    
    if type(remove) == list:
        train_decoys_test = train_decoys.drop(remove, axis=1, errors = 'ignore')

    if 'filename' in train_decoys_test.columns:
        train_decoys_test = train_decoys_test.drop(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
    else:
        train_decoys_test = train_decoys_test.drop(
            ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        
    #Get rid of colinear features
    sds = train_decoys_test.apply(np.std, axis = 0)
    train_decoys_test = train_decoys_test[train_decoys_test.columns[sds != 0]]
    
    new_scores = grid.decision_function(train_decoys_test)
    
    train_decoys['SVM_score'] = new_scores

    return(train_power, train_std, true_power, real_df, train_decoys)


##########


def do_lda(df_all, train_decoys, total_iter=10, p=0.5, alpha=0.01, train_alpha=0.01, remove=None, top_positive=True, qda=False):

    train_targets = df_all[~(df_all.index.isin(train_decoys.index))].copy()
    train_targets.loc[:, 'Label'] = 1

    train_df = pd.concat([train_decoys, train_targets]).reset_index(drop=True)
    train_df = train_df.sample(frac=1).reset_index(drop=True)
    
    if type(remove) == list:
        train_df.drop(remove, axis=1, inplace=True, errors = 'ignore')
    if 'SVM_score' in train_df.columns:
        train_df = train_df.sort_values(
            by = 'SVM_score', ascending=False).reset_index(drop = True)
    elif 'TailorScore' in train_df.columns:
        train_df = train_df.sort_values(
            by='TailorScore', ascending=False).reset_index(drop=True)
    else:
        train_df = train_df.sort_values(
            by='XCorr', ascending=False).reset_index(drop=True)

    #real df
    real_df = df_all[~(df_all.index.isin(train_decoys.index))
                     ].copy().reset_index(drop=True)
    if type(remove) == list:
        real_df.drop(remove, axis=1, inplace=True, errors = 'ignore')

    #Preprocess dataframe
    SVM_train_labels = train_df['Label'].copy()
    if 'filename' in train_df.columns:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
    else:
        SVM_train_features = train_df.drop(
            ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        
    #Get rid of colinear features
    sds = SVM_train_features.apply(np.std, axis = 0)
    SVM_train_features = SVM_train_features[SVM_train_features.columns[sds != 0]]

    #getting initial positive and negative set
    q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                           SVM_train_labels == 1, c=1 - p/2, lam=1 - p/2)
    if top_positive:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
    else:
        positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
    negative_set_indxs = (SVM_train_labels == -1)
    
    while sum(positive_set_indxs) == 0:
        train_alpha = train_alpha + 0.005
        #getting initial positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=1 - p/2, lam=1 - p/2)
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
        negative_set_indxs = (SVM_train_labels == -1)

    SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
        drop=True).copy()
    SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
        drop=True).copy()

    SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
    SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

    SVM_train_features_iter.reset_index(drop=True, inplace=True)
    SVM_train_labels_iter.reset_index(drop=True, inplace=True)

    train_power, train_std, true_power = [
        0]*total_iter, [0]*total_iter, [0]*total_iter

    logging.info("Conducting iterative lda.")
    sys.stderr.write("Conducting iterative lda.\n")

    for iterate in range(total_iter):
        logging.info("iteration: %s." % (iterate))
        sys.stderr.write("iteration: %s.\n" % (iterate))
        #determining best direction with cross validation for parameter selection
        grid = train_lda_cv(SVM_train_labels_iter, SVM_train_features_iter)
        
        #best_train_power = max(grid.cv_results_['mean_test_score'])
        #best_train_std = max(grid.cv_results_['std_test_score'])
        #train_power[iterate] = best_train_power
        #train_std[iterate] = best_train_std

        #the new direction
        new_scores = grid.decision_function(SVM_train_features)

        new_idx = pd.Series(new_scores).sort_values(ascending=False).index

        SVM_train_features = SVM_train_features.loc[new_idx].reset_index(
            drop=True)

        SVM_train_labels = SVM_train_labels.loc[new_idx].reset_index(drop=True)

        #determine the new positive and negative set
        q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                               SVM_train_labels == 1, c=1 - p/2, lam=1 - p/2)
        
        if top_positive:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
        else:
            positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= alpha)
        negative_set_indxs = (SVM_train_labels == -1)
        
        while sum(positive_set_indxs) == 0:
            train_alpha = train_alpha + 0.005
            #getting initial positive and negative set
            q_vals = uf.TDC_flex_c(SVM_train_labels == -1,
                                   SVM_train_labels == 1, c=1 - p/2, lam=1 - p/2)
            if top_positive:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha) & (SVM_train_features['rank'] == min(SVM_train_features['rank']))
            else:
                positive_set_indxs = (SVM_train_labels == 1) & (q_vals <= train_alpha)
            negative_set_indxs = (SVM_train_labels == -1)

        SVM_train_features_iter = SVM_train_features.loc[positive_set_indxs | negative_set_indxs, :].reset_index(
            drop=True).copy()
        SVM_train_labels_iter = SVM_train_labels.loc[positive_set_indxs | negative_set_indxs].reset_index(
            drop=True).copy()

        SVM_train_features_iter = SVM_train_features_iter.sample(frac=1)
        SVM_train_labels_iter = SVM_train_labels_iter.loc[SVM_train_features_iter.index]

        SVM_train_features_iter.reset_index(drop=True, inplace=True)
        SVM_train_labels_iter.reset_index(drop=True, inplace=True)

        #get actual power if we were to stop here
        real_labels = real_df['Label'].copy()
        if 'filename' in real_df.columns:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        else:
            real_df_test = real_df.drop(
                ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
            
        #Get rid of colinear features
        sds = real_df_test.apply(np.std, axis = 0)
        real_df_test = real_df_test[real_df_test.columns[sds != 0]]

        #the new direction
        new_scores = grid.decision_function(real_df_test)
        new_idx = pd.Series(new_scores).sort_values(ascending=False).index
        new_labels = real_labels.loc[new_idx].reset_index(drop=True)

        q_val = uf.TDC_flex_c(
            new_labels == -1, new_labels == 1, c=1/(2 - p), lam=1/(2 - p))
        power_final = sum((q_val <= alpha) & (new_labels == 1))
        true_power[iterate] = power_final

        logging.info("Observed power: %s." % (power_final))
        sys.stderr.write("Observed power: %s.\n" % (power_final))

        #logging.info("Trained power: %s." % (best_train_power))
        #sys.stderr.write("Trained power: %s.\n" % (best_train_power))

        #logging.info("Std trained power: %s." % (best_train_std))
        #sys.stderr.write("Std trained power: %s.\n" % (best_train_std))

    #using the last new_idx to report the discoveries
    real_df['SVM_score'] = new_scores
    
    if type(remove) == list:
        train_decoys_test = train_decoys.drop(remove, axis=1, errors = 'ignore')

    if 'filename' in train_decoys_test.columns:
        train_decoys_test = train_decoys_test.drop(
            ['SpecId', 'Label', 'filename', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
    else:
        train_decoys_test = train_decoys_test.drop(
            ['SpecId', 'Label', 'ScanNr', 'Peptide', 'Proteins'], axis=1).copy()
        
    #Get rid of colinear features
    sds = train_decoys_test.apply(np.std, axis = 0)
    train_decoys_test = train_decoys_test[train_decoys_test.columns[sds != 0]]
    
    new_scores = grid.decision_function(train_decoys_test)
    
    train_decoys['SVM_score'] = new_scores


    return(train_decoys, real_df)
    