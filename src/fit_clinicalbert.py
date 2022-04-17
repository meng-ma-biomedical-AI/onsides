"""
fit_clinicalbert.py

Use clinical bert to classify terms as events or not_events.

@author Nicholas Tatonetti, Tatonetti Lab (heavily inspired by https://towardsdatascience.com/text-classification-with-bert-in-pytorch-887965e5820f)
"""

import csv
import time
import torch
import random
from torch import nn
from torch.optim import Adam
from transformers import AutoTokenizer, AutoModel

import argparse
import numpy as np
import pandas as pd
from tqdm import tqdm

labels = {'not_event': 0, 'is_event': 1}
_PRETRAINED_PATH_ = "./models/Bio_ClinicalBERT"

print(f"Loading ClinicalBERT tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(_PRETRAINED_PATH_)

class Dataset(torch.utils.data.Dataset):

    def __init__(self, df, examples_only=False, _max_length=128):

        if not examples_only:
            self.labels = [labels[label] for label in df['class']]
        else:
            self.labels = [0 for _ in range(len(df))]

        self.texts = [tokenizer(text,
                                padding='max_length',
                                max_length=_max_length,
                                truncation=True,
                                return_tensors="pt") for text in df['string']]

    def classes(self):
        return self.labels

    def __len__(self):
        return len(self.labels)

    def get_batch_labels(self, idx):
        # Fetch a batch of labels
        return np.array(self.labels[idx])

    def get_batch_texts(self, idx):
        return self.texts[idx]

    def __getitem__(self, idx):

        batch_texts = self.get_batch_texts(idx)
        batch_y = self.get_batch_labels(idx)

        return batch_texts, batch_y

class ClinicalBertClassifier(nn.Module):

    def __init__(self, dropout=0.5):

        super(ClinicalBertClassifier, self).__init__()

        self.bert = AutoModel.from_pretrained(_PRETRAINED_PATH_)
        self.dropout = nn.Dropout(dropout)
        self.linear = nn.Linear(768, 2)
        self.relu = nn.ReLU()

    def forward(self, input_id, mask):

        _, pooled_output = self.bert(input_ids=input_id, attention_mask=mask, return_dict=False)
        dropout_output = self.dropout(pooled_output)
        linear_output = self.linear(dropout_output)
        final_layer = self.relu(linear_output)

        return final_layer

def train(model, train_data, val_data, learning_rate, epochs, max_length, batch_size, model_filename):

    train, val = Dataset(train_data, _max_length=max_length), Dataset(val_data, _max_length=max_length)

    train_dataloader = torch.utils.data.DataLoader(train, batch_size=batch_size, shuffle=True)
    val_dataloader = torch.utils.data.DataLoader(val, batch_size=batch_size)

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")

    print(f"Using device: {device}")

    criterion = nn.CrossEntropyLoss()
    optimizer = Adam(model.parameters(), lr=learning_rate)

    if use_cuda:
        model = model.cuda()
        criterion = criterion.cuda()

    best_val_acc = 0.0
    train_accuracies = list()
    train_losses = list()
    valid_accuracies = list()
    valid_losses = list()
    epoch_times = list()

    for epoch_num in range(epochs):

        total_acc_train = 0
        total_loss_train = 0
        epoch_start_time = time.time()

        for train_input, train_label in tqdm(train_dataloader):

            train_label = train_label.to(device)
            mask = train_input['attention_mask'].to(device)
            input_id = train_input['input_ids'].squeeze(1).to(device)

            output = model(input_id, mask)

            batch_loss = criterion(output, train_label)
            total_loss_train += batch_loss.item()

            acc = (output.argmax(dim=1) == train_label).sum().item()
            total_acc_train += acc

            model.zero_grad()
            batch_loss.backward()
            optimizer.step()

        total_acc_val = 0
        total_loss_val = 0

        with torch.no_grad():

            for val_input, val_label in val_dataloader:

                val_label = val_label.to(device)
                mask = val_input['attention_mask'].to(device)
                input_id = val_input['input_ids'].squeeze(1).to(device)

                output = model(input_id, mask)

                batch_loss = criterion(output, val_label)
                total_loss_val += batch_loss.item()

                acc = (output.argmax(dim=1) == val_label).sum().item()
                total_acc_val += acc

                if acc > best_val_acc:
                    best_val_acc = acc
                    torch.save(model.state_dict(), model_filename)


        train_losses.append(total_loss_train / len(train_data))
        train_accuracies.append(total_acc_train / len(train_data))
        valid_losses.append(total_loss_val / len(val_data))
        valid_accuracies.append(total_acc_val / len(val_data))
        epoch_times.append(time.time()-epoch_start_time)

        print(f'Epochs: {epoch_num + 1} | Train Loss: {total_loss_train / len(train_data): .4f} \
                | Train Accuracy: {total_acc_train / len(train_data): .4f} \
                | Val Loss: {total_loss_val / len(val_data): .4f} \
                | Val Accuracy: {total_acc_val / len(val_data): .4f}')

    return train_losses, train_accuracies, valid_losses, valid_accuracies, epoch_times

def evaluate(model, test_data, max_length, batch_size, examples_only=False):

    test = Dataset(test_data, examples_only, _max_length=max_length)

    test_dataloader = torch.utils.data.DataLoader(test, batch_size=batch_size)

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")

    if use_cuda:

        model = model.cuda()

    total_acc_test = 0
    outputs = list()

    with torch.no_grad():

        for test_input, test_label in tqdm(test_dataloader):

              test_label = test_label.to(device)
              mask = test_input['attention_mask'].to(device)
              input_id = test_input['input_ids'].squeeze(1).to(device)

              output = model(input_id, mask)
              outputs.append(output)

              acc = (output.argmax(dim=1) == test_label).sum().item()
              total_acc_test += acc

    if not examples_only:
        print(f'Test Accuracy: {total_acc_test / len(test_data): .4f}')

    return outputs

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--ref', help="relative or full path to the reference set", type=str, required=True)
    parser.add_argument('--max-length', help="maximum number of tokens to use as input for ClinicalBERT", type=int, default=128)
    parser.add_argument('--batch-size', help="batch size to feed into the model each epoch, will need to balance with max_length to avoid memory errors", type=int, default=128)
    parser.add_argument('--epochs', help="number of epochs to train, default is 25", type=int, default=25)
    parser.add_argument('--learning-rate', help="the learning rate to use, default is 1e-6", type=float, default=1e-6)
    parser.add_argument('--ifexists', help="what to do if model already exists with same parameters, options are 'replicate', 'overwrite', 'quit' - default is 'quit'", type=str, default='quit')

    args = parser.parse_args()

    print(f"Loading reference data...")

    # datapath = './data/clinical_bert_reference_set.txt'
    datapath = args.ref
    df = pd.read_csv(datapath)
    print(df.head())
    print(len(df))

    print("Splitting data into training, validation, and testing...")
    refset = int(args.ref.split('ref')[1].split('_')[0])
    np_random_seed = 222
    random_state = 24
    max_length = args.max_length
    batch_size = args.batch_size
    EPOCHS = args.epochs
    LR = args.learning_rate

    # check for existing model file
    filename_params = f'{refset}_{np_random_seed}_{random_state}_{EPOCHS}_{LR}_{max_length}_{batch_size}'
    final_model_filename = f'./models/final-bydrug_{filename_params}.pth'
    if os.path.exists(final_model_filename):
        print("Found final model already saved at path: {file_model_filename}")
        if args.ifexists == 'quit':
            print("  Quitting. To run a replicate, use --ifexists replicate option.")
            sys.exit(1)
        elif args.ifexists == 'replicate':
            print("  Will run a replicate, checking for any existing replicates...")
            reps = [f for f in os.listdir('./models/') if f.find(filename_params) != 0]
            filename_params = f'{refset}_{np_random_seed}_{random_state}_{EPOCHS}_{LR}_{max_length}_{batch_size}_rep{len(reps)}'
            final_model_filename = f'./models/final-bydrug_{filename_params}.pth'
            print(f"    Found {len(reps)} existing models. Filename for this replicate will be: {final_model_filename}")
        elif args.ifexists == 'overwrite':
            print("  Option is to overwrite the exising model file.")
            confirm = input("!!! Please confirm that you would really like to overwrite the existing file? [y/N]")
            if confirm != 'y':
                print("  Okay, will not overwrite the file. Quitting instead.")
                sys.exit(1)
        else:
            raise Exception("ERROR: Unexpected option set for --ifexists argument: {args.ifexists}")


    np.random.seed(np_random_seed)

    # randomly select by row
    #df_train, df_val, df_test = np.split(df.sample(frac=1, random_state=random_state),
    #                                     [int(0.8*len(df)), int(0.9*len(df))])

    # randomly select by drug/label
    druglist = sorted(set(df['drug']))

    random.seed(np_random_seed)
    random.shuffle(druglist)

    drugs_train, drugs_val, drugs_test = np.split(druglist, [int(0.8*len(druglist)), int(0.9*len(druglist))])

    print(f"Split labels in train, val, test by drug:")
    print(len(drugs_train), len(drugs_val), len(drugs_test))

    df_train = df[df['drug'].isin(drugs_train)]
    df_val = df[df['drug'].isin(drugs_val)]
    df_test = df[df['drug'].isin(drugs_test)]

    print(f"Resulting dataframes have sizes:")
    print(len(df_train), len(df_val), len(df_test))

    model = ClinicalBertClassifier()

    print("Fitting the model...")
    best_epoch_model_filename = f'./models/final-bydrug_{filename_params}_BestEpoch.pth'

    training_results = train(model, df_train, df_val, LR, EPOCHS, max_length, batch_size, best_epoch_model_filename)

    print("Saving the model to file...")

    torch.save(model.state_dict(), final_model_filename)

    print("Saving loss and accuracies for each epoch to file...")
    lafh = open(f'./results/epoch-results_{filename_params}.csv', 'w')
    writer = csv.writer(lafh)
    writer.writerow(['epoch', 'train_loss', 'train_accuracy', 'valid_loss', 'valid_accuracy', 'epoch_time'])
    for epoch in range(EPOCHS):
        writer.writerow([epoch+1] + [training_results[i][epoch] for i in range(len(training_results))])
    lafh.close()

    print("Loading the model from file...")

    loaded_model = ClinicalBertClassifier()
    loaded_model.load_state_dict(torch.load(final_model_filename))

    print("Evaluating the model on the held out test set...")
    evaluate(loaded_model, df_test, max_length, batch_size)
